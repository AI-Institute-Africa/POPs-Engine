"""Suspected fakes: what the risk engine flagged, for admins to decide."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.money import format_minor
from app.core.security import Principal, Role, require, sign_evidence
from app.models import Claim, Evidence, FraudAlert, Obligation
from app.review import service as review

router = APIRouter(prefix="/fraud", tags=["suspected fakes"])
DB = Annotated[Session, Depends(get_session)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


class AlertOut(BaseModel):
    alert_id: uuid.UUID
    claim_id: uuid.UUID
    level: str
    score: int
    signals: list[dict[str, Any]]
    status: str
    resolved_by: str | None
    resolution_note: str | None
    resolved_at: datetime | None
    created_at: datetime
    claim_state: str
    claimant_ref: str
    channel: str
    declared: str | None
    obligation: str
    preview_url: str | None
    forensics_url: str | None


class ResolveIn(BaseModel):
    resolution: Literal["confirmed_fake", "false_positive"]
    note: str = Field(min_length=3, max_length=2000)


def _out(db: Session, a: FraudAlert) -> AlertOut:
    claim = db.get(Claim, a.claim_id)
    assert claim is not None
    ob = db.get(Obligation, claim.obligation_id)
    ev = db.scalars(
        select(Evidence)
        .where(Evidence.claim_id == claim.claim_id)
        .order_by(Evidence.received_at.desc())
    ).first()

    def url(variant: str) -> str | None:
        if ev is None or (variant == "forensics" and not ev.forensics_key):
            return None
        sig = sign_evidence(ev.evidence_id)
        return f"/evidence/{ev.evidence_id}/content?variant={variant}&sig={sig}"

    return AlertOut(
        alert_id=a.alert_id,
        claim_id=a.claim_id,
        level=a.level,
        score=a.score,
        signals=a.signals,
        status=a.status,
        resolved_by=a.resolved_by,
        resolution_note=a.resolution_note,
        resolved_at=a.resolved_at,
        created_at=a.created_at,
        claim_state=claim.state,
        claimant_ref=claim.claimant_ref,
        channel=claim.channel,
        declared=format_minor(claim.declared_amount_minor, claim.declared_currency or "USD")
        if claim.declared_amount_minor is not None
        else None,
        obligation=f"{ob.description or ob.type} · {ob.learner_ref}" if ob else "",
        preview_url=url("preview"),
        forensics_url=url("forensics"),
    )


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    db: DB, p: Reviewer, status: str = "open", level: str | None = None, limit: int = 200
) -> list[AlertOut]:
    q = select(FraudAlert).where(FraudAlert.org_id == p.org_id)
    if status != "all":
        q = q.where(FraudAlert.status == status)
    if level:
        q = q.where(FraudAlert.level == level)
    rows = db.scalars(q.order_by(FraudAlert.score.desc(), FraudAlert.created_at).limit(limit))
    return [_out(db, a) for a in rows]


@router.get("/alerts/summary")
def alert_summary(db: DB, p: Reviewer) -> dict:
    rows = db.execute(
        select(FraudAlert.level, func.count())
        .where(FraudAlert.org_id == p.org_id, FraudAlert.status == "open")
        .group_by(FraudAlert.level)
    ).all()
    counts = {level: n for level, n in rows}
    return {"open_high": counts.get("high", 0), "open_medium": counts.get("medium", 0)}


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: uuid.UUID, body: ResolveIn, db: DB, p: Admin) -> AlertOut:
    alert = db.scalars(
        select(FraudAlert).where(FraudAlert.alert_id == alert_id, FraudAlert.org_id == p.org_id)
    ).first()
    if alert is None:
        raise HTTPException(404, "alert not found")
    try:
        review.resolve_alert(db, p, alert, body.resolution, body.note)
    except review.ReviewError as exc:
        db.rollback()
        raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
    db.commit()
    return _out(db, alert)
