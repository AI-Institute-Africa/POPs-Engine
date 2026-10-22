"""WhatsApp Business (Cloud API) intake: payers send their proof of payment to our number.

A message becomes an ordinary claim on the obligation whose payment code it carries (in the
caption, the text, or a text sent shortly before the photo). The claim then runs the same
pipeline as an upload: a WhatsApp photo is still only a claim, never confirmation. Replies
only acknowledge receipt; they never say a payment is verified.
"""

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal, Role
from app.extraction.templates import parse_text
from app.intake.files import RejectedFile
from app.intake.scan import ScannerUnavailable
from app.intake.service import create_claim
from app.models import AuditEvent, Claim, Obligation
from app.payment_codes.codes import find_codes

log = logging.getLogger(__name__)

ACTOR = "channel:whatsapp"
CODE_MEMORY = timedelta(minutes=30)  # a code sent as text applies to a photo sent soon after
MEDIA_TYPES = ("image", "document")


@dataclass(frozen=True)
class SenderPrincipal(Principal):
    """The WhatsApp sender, acting as a submitter on the obligation's organisation."""

    wa_id: str = ""

    @property
    def actor(self) -> str:
        return f"whatsapp:{self.wa_id}"[:80]


def verify_signature(body: bytes, header: str | None) -> bool:
    secret = get_settings().whatsapp_app_secret
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.removeprefix("sha256="))


def _http() -> httpx.Client:
    token = get_settings().whatsapp_access_token
    return httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30)


def download_media(media_id: str) -> bytes:
    """Two steps (Graph API): the media id resolves to a short-lived URL, then the bytes."""
    s = get_settings()
    with _http() as http:
        meta = http.get(f"{s.whatsapp_graph_url}/{media_id}")
        meta.raise_for_status()
        data = http.get(meta.json()["url"])
        data.raise_for_status()
        if len(data.content) > s.max_upload_bytes:
            raise RejectedFile("TOO_LARGE", "file too large")
        return data.content


def send_reply(to: str, text: str) -> None:
    """Best effort: a failed reply is logged, never retried into a duplicate claim."""
    s = get_settings()
    if not (s.whatsapp_access_token and s.whatsapp_phone_number_id):
        return
    try:
        with _http() as http:
            r = http.post(
                f"{s.whatsapp_graph_url}/{s.whatsapp_phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
            )
            r.raise_for_status()
    except httpx.HTTPError:
        log.warning("WhatsApp reply to %s failed", to[-4:], exc_info=True)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:64]


def _obligation(session: Session, codes: list[str]) -> Obligation | None:
    if not codes:
        return None
    q = select(Obligation).where(Obligation.payment_code.in_(codes))
    org = get_settings().whatsapp_org_id
    if org:
        q = q.where(Obligation.org_id == uuid.UUID(org))
    return session.scalars(q).first()


def _remembered(session: Session, wa_id: str) -> Obligation | None:
    """A payment code this sender texted in the last few minutes, before sending the photo."""
    ev = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.entity_type == "whatsapp_sender",
            AuditEvent.entity_id == _hash(wa_id),
            AuditEvent.action == "whatsapp.code_noted",
            AuditEvent.created_at > datetime.now(UTC) - CODE_MEMORY,
        )
        .order_by(AuditEvent.created_at.desc())
    ).first()
    if ev is None or not ev.after:
        return None
    return session.get(Obligation, uuid.UUID(ev.after["obligation_id"]))


@dataclass
class Outcome:
    reply: str | None
    claim_id: uuid.UUID | None = None


def _text_of(msg: dict[str, Any]) -> str:
    kind = msg.get("type")
    if kind == "text":
        return str((msg.get("text") or {}).get("body") or "")
    if kind in MEDIA_TYPES:
        return str((msg.get(kind) or {}).get("caption") or "")
    return ""


def handle_message(session: Session, msg: dict[str, Any]) -> Outcome:
    """One inbound message. The caller commits, then dispatches the pipeline and the reply."""
    message_id, wa_id = str(msg.get("id", "")), str(msg.get("from", ""))
    kind = str(msg.get("type", ""))
    if not message_id or not wa_id:
        return Outcome(None)
    # Meta redelivers webhooks: a message id is handled once
    if session.scalars(
        select(AuditEvent.event_id).where(
            AuditEvent.entity_type == "whatsapp_message",
            AuditEvent.entity_id == _hash(message_id),
        )
    ).first():
        return Outcome(None)

    text = _text_of(msg)
    obligation = _obligation(session, find_codes(text))
    has_media = kind in MEDIA_TYPES
    # a text that is only the code (no payment details): remember it for the photo that follows
    is_proof_text = kind == "text" and "amount" in parse_text(text)
    if obligation is not None and kind == "text" and not is_proof_text:
        audit(
            session,
            actor=ACTOR,
            action="whatsapp.code_noted",
            entity_type="whatsapp_sender",
            entity_id=_hash(wa_id),
            org_id=obligation.org_id,
            after={"obligation_id": str(obligation.obligation_id)},
        )
        _seen(session, message_id, obligation.org_id, None)
        return Outcome(f"Thanks. Now send the proof of payment for {obligation.payment_code}.")
    if obligation is None and has_media:
        obligation = _remembered(session, wa_id)
    if obligation is None:
        _seen(session, message_id, None, None)
        return Outcome(
            "Please send your proof of payment with your payment code (for example CG-H4CC093) "
            "in the caption. You will find the code on your invoice."
        )
    if not has_media and not is_proof_text:
        _seen(session, message_id, obligation.org_id, None)
        return Outcome("Please send a screenshot, PDF or the payment SMS as your proof.")

    s = get_settings()
    try:
        ratelimit.check("whatsapp", wa_id, s.rate_limit_uploads_per_minute, 60)
        data = download_media(str(msg[kind]["id"])) if has_media else text.encode("utf-8")
        p = SenderPrincipal(
            user_id=uuid.uuid5(uuid.NAMESPACE_URL, f"whatsapp:{wa_id}"),
            org_id=obligation.org_id,
            role=Role.SUBMITTER,
            email="",
            wa_id=wa_id,
        )
        claim = create_claim(
            session,
            p,
            obligation_id=obligation.obligation_id,
            claimant_ref=obligation.learner_ref,
            channel="whatsapp",
            data=data,
            declared_amount_minor=None,
            declared_currency=obligation.currency,
            declared_reference=None,
            source_type="whatsapp",
        )
    except ratelimit.RateLimited:
        return Outcome("You are sending too fast. Please wait a minute and try again.")
    except RejectedFile as exc:
        _seen(session, message_id, obligation.org_id, None)
        return Outcome(f"We could not accept that file: {exc}")
    except (httpx.HTTPError, ScannerUnavailable):
        # not marked seen: Meta's redelivery retries it
        log.warning("WhatsApp media %s could not be fetched or scanned", message_id, exc_info=True)
        raise
    _seen(session, message_id, obligation.org_id, claim)
    return Outcome(
        f"Received your proof of payment for {obligation.payment_code}. We will confirm it once "
        "the money reaches our account.",
        claim.claim_id,
    )


def _seen(session: Session, message_id: str, org_id: uuid.UUID | None, claim: Claim | None) -> None:
    audit(
        session,
        actor=ACTOR,
        action="whatsapp.message",
        entity_type="whatsapp_message",
        entity_id=_hash(message_id),
        org_id=org_id,
        after={"claim_id": str(claim.claim_id)} if claim else None,
    )


def messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Inbound messages in a Cloud API webhook (status updates are ignored)."""
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            out += (change.get("value") or {}).get("messages") or []
    return out
