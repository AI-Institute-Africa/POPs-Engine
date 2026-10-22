"""Recipient-side payment alerts.

Device/mailbox endpoints authenticate with the source key (header X-POPs-Source-Key), not a
user token, so an SMS-forwarder app on the merchant phone can post directly. They accept the
JSON shapes common forwarder apps send ({"from","text"}, {"sender","message"}, {"body"} ...).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts import service as alerts
from app.core import ratelimit
from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import Principal, Role, require
from app.models import AlertMessage, AlertSource
from app.normalise.fields import parse_datetime
from app.workers.tasks import dispatch_rematch

router = APIRouter(prefix="/alerts", tags=["payment alerts"])
DB = Annotated[Session, Depends(get_session)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


def _source(db: Session, key: str | None, kind: str) -> AlertSource:
    src = alerts.authenticate(db, key)
    if src is None:
        raise HTTPException(401, "unknown or inactive alert source key")
    if src.kind != kind:
        raise HTTPException(403, f"this key belongs to an {src.kind} source")
    try:
        ratelimit.check(
            "alerts", str(src.source_id), get_settings().rate_limit_alerts_per_minute, 60
        )
    except ratelimit.RateLimited as exc:
        raise HTTPException(
            429, "too many alerts", headers={"Retry-After": str(exc.retry_after)}
        ) from exc
    return src


def _first(d: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _out(m: AlertMessage) -> dict:
    return {
        "message_id": m.message_id,
        "status": m.status,
        "reason": m.reason,
        "credit_id": m.credit_id,
        "parsed": m.parsed,
    }


def _after_credit(db: Session, src: AlertSource, result: alerts.IngestResult) -> dict:
    db.commit()
    if result.message.status == "credited":
        dispatch_rematch(src.org_id)  # match waiting claims within seconds of the alert
    return _out(result.message)


@router.post("/sms")
async def receive_sms(
    request: Request, db: DB, x_pops_source_key: Annotated[str | None, Header()] = None
) -> dict:
    src = _source(db, x_pops_source_key, "sms")
    if "json" in request.headers.get("content-type", ""):
        payload = await request.json()
    else:
        payload = dict(await request.form())
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected a JSON object")
    sender = _first(payload, "from", "sender", "address", "phone", "originator")
    text = _first(payload, "text", "message", "body", "msg", "content")
    if not text:
        raise HTTPException(422, "no message text")
    when = parse_datetime(_first(payload, "received_at", "sentStamp", "receivedStamp", "date"))
    result = alerts.ingest(db, src, sender=sender or "unknown", body=text, received_at=when)
    return _after_credit(db, src, result)


@router.post("/email")
async def receive_email(
    request: Request, db: DB, x_pops_source_key: Annotated[str | None, Header()] = None
) -> dict:
    """Raw RFC 822 message, e.g. from an inbound-mail webhook (Mailgun, SES, Postmark)."""
    src = _source(db, x_pops_source_key, "email")
    raw = await request.body()
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise HTTPException(422, "expected a raw email under 5 MB")
    return _after_credit(db, src, alerts.ingest_email(db, src, raw))


@router.post("/heartbeat")
def heartbeat(db: DB, x_pops_source_key: Annotated[str | None, Header()] = None) -> dict:
    src = alerts.authenticate(db, x_pops_source_key)
    if src is None:
        raise HTTPException(401, "unknown or inactive alert source key")
    src.last_seen_at = datetime.now(UTC)
    src.stale_notified_at = None
    db.commit()
    return {"ok": True}


# --- management --------------------------------------------------------------------------------


class SourceIn(BaseModel):
    kind: str = Field(pattern="^(sms|email)$")
    name: str = Field(min_length=3, max_length=120)
    provider: str = Field(min_length=2, max_length=40)
    account_alias: str = Field(min_length=1, max_length=80)
    allowed_senders: list[str] = Field(default_factory=list)
    require_dkim: bool = True
    template: str | None = None


def _source_out(s: AlertSource) -> dict:
    stale_after = get_settings().alert_source_stale_minutes * 60
    seen = s.last_seen_at
    return {
        "source_id": s.source_id,
        "kind": s.kind,
        "name": s.name,
        "provider": s.provider,
        "account_alias": s.account_alias,
        "allowed_senders": s.allowed_senders,
        "require_dkim": s.require_dkim,
        "template": s.template,
        "active": s.active,
        "last_seen_at": seen,
        "last_message_at": s.last_message_at,
        "stale": bool(
            s.active and (seen is None or (datetime.now(UTC) - seen).total_seconds() > stale_after)
        ),
    }


@router.get("/sources")
def list_sources(db: DB, p: Reviewer) -> list[dict]:
    rows = db.scalars(
        select(AlertSource).where(AlertSource.org_id == p.org_id).order_by(AlertSource.created_at)
    )
    return [_source_out(s) for s in rows]


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, db: DB, p: Admin) -> dict:
    if body.kind == "sms" and not body.allowed_senders:
        raise HTTPException(422, "list the SMS sender IDs to accept (e.g. 'EcoCash')")
    key, digest = alerts.new_key()
    src = AlertSource(
        org_id=p.org_id,
        kind=body.kind,
        name=body.name,
        provider=body.provider.upper(),
        account_alias=body.account_alias,
        key_hash=digest,
        allowed_senders=body.allowed_senders,
        require_dkim=body.require_dkim,
        template=body.template,
        created_by=p.actor,
    )
    db.add(src)
    db.flush()
    audit(
        db,
        actor=p.actor,
        action="alert_source.created",
        entity_type="alert_source",
        entity_id=src.source_id,
        org_id=p.org_id,
        after=body.model_dump(),
    )
    db.commit()
    return {
        **_source_out(src),
        "key": key,
        "note": "Store this key in the forwarder now; it is not shown again.",
    }


def _own(db: Session, p: Principal, source_id: uuid.UUID) -> AlertSource:
    src = db.scalars(
        select(AlertSource).where(
            AlertSource.source_id == source_id, AlertSource.org_id == p.org_id
        )
    ).first()
    if src is None:
        raise HTTPException(404, "alert source not found")
    return src


@router.post("/sources/{source_id}/rotate-key")
def rotate_key(source_id: uuid.UUID, db: DB, p: Admin) -> dict:
    src = _own(db, p, source_id)
    key, src.key_hash = alerts.new_key()
    audit(
        db,
        actor=p.actor,
        action="alert_source.key_rotated",
        entity_type="alert_source",
        entity_id=src.source_id,
        org_id=p.org_id,
    )
    db.commit()
    return {**_source_out(src), "key": key}


@router.post("/sources/{source_id}/deactivate")
def deactivate(source_id: uuid.UUID, db: DB, p: Admin) -> dict:
    src = _own(db, p, source_id)
    src.active = False
    audit(
        db,
        actor=p.actor,
        action="alert_source.deactivated",
        entity_type="alert_source",
        entity_id=src.source_id,
        org_id=p.org_id,
    )
    db.commit()
    return _source_out(src)


@router.get("/messages")
def list_messages(db: DB, p: Reviewer, status: str | None = None, limit: int = 200) -> list[dict]:
    q = select(AlertMessage).where(AlertMessage.org_id == p.org_id)
    if status:
        q = q.where(AlertMessage.status == status)
    rows = db.scalars(q.order_by(AlertMessage.created_at.desc()).limit(min(limit, 1000)))
    return [
        {
            **_out(m),
            "sender": m.sender,
            "subject": m.subject,
            "body": m.body,
            "received_at": m.received_at,
            "source_id": m.source_id,
        }
        for m in rows
    ]
