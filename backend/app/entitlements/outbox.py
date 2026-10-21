"""Cognify entitlement events via a transactional outbox.

The engine reports balances; Cognify's billing rules decide assessment access (blueprint 9:
"do not invent new rules in the payment engine"). Events are written in the same transaction as
the allocation, and delivery is retried with a stable event id so Cognify can dedupe.
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ledger.service import Balance
from app.models import Obligation, OutboxEvent

MAX_ATTEMPTS = 10


def enqueue_entitlement(
    session: Session, obligation: Obligation, bal: Balance, event_key: str, cause: str
) -> None:
    if session.scalars(select(OutboxEvent).where(OutboxEvent.event_key == event_key)).first():
        return
    session.add(
        OutboxEvent(
            org_id=obligation.org_id,
            event_type="entitlement.updated",
            event_key=event_key,
            payload={
                "obligation_id": str(obligation.obligation_id),
                "external_ref": obligation.external_ref,
                "learner_ref": obligation.learner_ref,
                "type": obligation.type,
                "currency": obligation.currency,
                "amount_minor": bal.amount_minor,
                "paid_minor": bal.paid_minor,
                "outstanding_minor": bal.outstanding_minor,
                "payment_status": bal.status,
                "cause": cause,
            },
        )
    )


def sign(body: bytes, secret: str, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def deliver_pending(session: Session, client: httpx.Client | None = None, limit: int = 50) -> int:
    """Deliver pending events; returns the number delivered. Safe to run concurrently."""
    s = get_settings()
    if not s.cognify_webhook_url:
        return 0
    events = session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending", OutboxEvent.attempts < MAX_ATTEMPTS)
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    delivered = 0
    http = client or httpx.Client(timeout=10)
    for ev in events:
        body = json.dumps(
            {"id": str(ev.event_id), "type": ev.event_type, "data": ev.payload},
            separators=(",", ":"),
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-POPs-Event-Id": str(ev.event_id),
            "X-POPs-Signature": sign(body, s.cognify_webhook_secret, int(time.time())),
        }
        ev.attempts += 1
        try:
            resp = http.post(s.cognify_webhook_url, content=body, headers=headers)
            resp.raise_for_status()
            ev.status = "delivered"
            ev.delivered_at = datetime.now(UTC)
            delivered += 1
        except httpx.HTTPError as exc:
            ev.last_error = str(exc)[:500]
            if ev.attempts >= MAX_ATTEMPTS:
                ev.status = "failed"
    session.commit()
    return delivered
