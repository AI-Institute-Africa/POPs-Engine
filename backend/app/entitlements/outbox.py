"""Cognify entitlement events via a transactional outbox.

The engine reports balances; Cognify's billing rules decide assessment access (blueprint 9:
"do not invent new rules in the payment engine"). Events are written in the same transaction as
the allocation, and delivery is retried with a stable event id so Cognify can dedupe.
"""

import hashlib
import hmac
import json
import smtplib
import time
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ledger.service import Balance
from app.models import Claim, Obligation, OutboxEvent

MAX_ATTEMPTS = 10


def entitlement_payload(obligation: Obligation, bal: Balance, cause: str) -> dict[str, Any]:
    """What the host system is told about an obligation's payment state."""
    return {
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
    }


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
            payload=entitlement_payload(obligation, bal, cause),
        )
    )


def enqueue_event(
    session: Session, org_id: uuid.UUID, event_type: str, event_key: str, payload: dict[str, Any]
) -> bool:
    """Queue an event once per `event_key`; returns False when it was already queued."""
    if session.scalars(select(OutboxEvent).where(OutboxEvent.event_key == event_key)).first():
        return False
    session.add(
        OutboxEvent(org_id=org_id, event_type=event_type, event_key=event_key, payload=payload)
    )
    return True


def enqueue_payer_message(session: Session, claim: Claim, kind: str, text: str) -> bool:
    """Queue a neutral status message to the payer, for claims that arrived over WhatsApp.

    The payload holds the claim id and the text only; the number is looked up when the message is
    sent, so it is never copied into the outbox.
    """
    if claim.channel != "whatsapp":
        return False
    return enqueue_event(
        session,
        claim.org_id,
        "payer.outcome",
        f"payer:{claim.claim_id}:{kind}",
        {"text": text, "claim_id": str(claim.claim_id), "kind": kind},
    )


def sign(body: bytes, secret: str, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _channels(event_type: str) -> list[str]:
    s = get_settings()
    if event_type.startswith(("fraud.", "ops.")):
        out = []
        if s.admin_alert_webhook_url:
            out.append("admin_webhook")
        if s.smtp_host and s.admin_alert_emails:
            out.append("email")
        return out
    if event_type.startswith("payer."):
        ready = s.whatsapp_access_token and s.whatsapp_phone_number_id
        return ["whatsapp"] if ready else []
    return ["cognify"] if s.cognify_webhook_url else []


def _email_wording(ev: OutboxEvent) -> tuple[str, str]:
    """Email subject and the console hint that fits the event."""
    if ev.event_type.startswith("fraud."):
        return (
            f"[POPs] Suspected fake proof of payment ({ev.payload.get('score')}/100)",
            "Open Suspected fakes in the POPs console to decide.",
        )
    if ev.event_type == "ops.review_pending":
        return "[POPs] Proof of payment needs review", "Open the Review queue in the POPs console."
    if ev.event_type == "ops.review_overdue":
        return (
            "[POPs] Proof of payment review overdue",
            "Open the Review queue in the POPs console.",
        )
    return "[POPs] Operations alert", ""


def _send(channel: str, ev: OutboxEvent, http: httpx.Client, session: Session) -> None:
    s = get_settings()
    if channel == "whatsapp":
        from app.whatsapp.service import send_reply

        claim = session.get(Claim, uuid.UUID(ev.payload["claim_id"]))
        if claim is None or not claim.submitted_by.startswith("whatsapp:"):
            return  # nobody to tell; counts as delivered rather than retrying forever
        send_reply(
            claim.submitted_by.removeprefix("whatsapp:"),
            ev.payload["text"],
            raise_on_error=True,
            client=http,
        )
        return
    if channel == "cognify":
        body = json.dumps(
            {"id": str(ev.event_id), "type": ev.event_type, "data": ev.payload},
            separators=(",", ":"),
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-POPs-Event-Id": str(ev.event_id),
            "X-POPs-Signature": sign(body, s.cognify_webhook_secret, int(time.time())),
        }
        http.post(s.cognify_webhook_url or "", content=body, headers=headers).raise_for_status()
    elif channel == "admin_webhook":
        # Slack/Teams/Google Chat incoming webhooks all accept a top-level "text"
        alert = {
            "text": ev.payload.get("text", ev.event_type),
            "event_id": str(ev.event_id),
            "type": ev.event_type,
            "data": ev.payload,
        }
        http.post(s.admin_alert_webhook_url or "", json=alert).raise_for_status()
    elif channel == "email":
        msg = EmailMessage()
        subject, hint = _email_wording(ev)
        msg["Subject"] = subject
        msg["From"] = s.smtp_from
        msg["To"] = ", ".join(s.admin_alert_emails)
        signals = ev.payload.get("signals")
        msg.set_content(
            "\n\n".join(
                part
                for part in (
                    str(ev.payload.get("text", "")),
                    f"Signals: {', '.join(signals)}" if signals else "",
                    hint,
                )
                if part
            )
        )
        with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password or "")
            smtp.send_message(msg)


def deliver_pending(session: Session, client: httpx.Client | None = None, limit: int = 50) -> int:
    """Deliver pending events to their channels; returns the number delivered.

    Entitlement events go to Cognify (signed). Fraud events go to the admin webhook and/or email.
    Events with no configured channel stay pending until one is configured. Safe to run
    concurrently (rows are locked with SKIP LOCKED).
    """
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
        channels = _channels(ev.event_type)
        if not channels:
            continue
        ev.attempts += 1
        try:
            for channel in channels:
                _send(channel, ev, http, session)
            ev.status = "delivered"
            ev.delivered_at = datetime.now(UTC)
            delivered += 1
        except (httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
            ev.last_error = str(exc)[:500]
            if ev.attempts >= MAX_ATTEMPTS:
                ev.status = "failed"
    session.commit()
    return delivered
