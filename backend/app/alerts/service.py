"""Recipient-side alert ingestion: trusted SMS forwarders and mailboxes become credits.

Controls (blueprint 4.7, "spoofed alert or SMS"):
- only registered sources with a valid key are accepted, and only from allowed senders;
- emails must carry a valid DKIM signature from an allowed sender (configurable per source);
- alert credits are capped at evidence tier E2; a statement line later upgrades them to E3;
- every alert is stored, credited or not, so finance can audit what arrived;
- the same money is never counted twice: alerts and statement lines are matched by reference,
  and a statement that contradicts an alert disputes the alert credit (it can't be allocated).
"""

import email
import email.policy
import functools
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.alerts.parse import NotACredit, parse
from app.claims.service import transition
from app.core import observability as obs
from app.core.audit import audit
from app.core.config import get_settings
from app.models import (
    AlertMessage,
    AlertSource,
    Allocation,
    Claim,
    ClaimState,
    Credit,
    OutboxEvent,
)
from app.normalise.fields import normalise_reference

log = logging.getLogger(__name__)


def new_key() -> tuple[str, str]:
    """Returns (key shown once to the admin, sha256 stored)."""
    key = "pops_ak_" + secrets.token_urlsafe(32)
    return key, hashlib.sha256(key.encode()).hexdigest()


def authenticate(session: Session, key: str | None) -> AlertSource | None:
    if not key or not key.startswith("pops_ak_"):
        return None
    digest = hashlib.sha256(key.encode()).hexdigest()
    src = session.scalars(select(AlertSource).where(AlertSource.key_hash == digest)).first()
    if src is None or not src.active or not hmac.compare_digest(src.key_hash, digest):
        return None
    return src


def _sender_allowed(src: AlertSource, sender: str) -> bool:
    if not src.allowed_senders:
        return True
    s = sender.strip().lower()
    for allowed in src.allowed_senders:
        a = allowed.strip().lower()
        if src.kind == "email" and a.startswith("@"):
            if s.endswith(a):
                return True
        elif re.sub(r"[\s-]", "", s) == re.sub(r"[\s-]", "", a):
            return True
    return False


@dataclass
class IngestResult:
    message: AlertMessage
    credit: Credit | None


def _digest(sender: str, subject: str | None, body: str, raw: bytes | None = None) -> str:
    # emails dedupe on their raw bytes, so a signed copy is never mistaken for an unsigned one
    basis = raw if raw is not None else f"{sender}\n{subject or ''}\n{body}".encode()
    return hashlib.sha256(basis).hexdigest()


def _record(
    session,
    src,
    sender,
    subject,
    body,
    received_at,
    status,
    reason,
    parsed=None,
    credit_id=None,
    digest: str | None = None,
) -> AlertMessage:
    digest = digest or _digest(sender, subject, body)
    existing = session.scalars(
        select(AlertMessage).where(
            AlertMessage.source_id == src.source_id, AlertMessage.body_sha256 == digest
        )
    ).first()
    if existing:
        return existing
    msg = AlertMessage(
        org_id=src.org_id,
        source_id=src.source_id,
        sender=sender[:200],
        subject=(subject or None) and subject[:300],
        body=body[:5000],
        body_sha256=digest,
        received_at=received_at,
        status=status,
        reason=reason,
        parsed=parsed,
        credit_id=credit_id,
    )
    session.add(msg)
    session.flush()
    obs.ALERTS_RECEIVED.labels(src.kind, status).inc()
    return msg


def _twin(session: Session, org_id: uuid.UUID, ref_norm: str) -> Credit | None:
    """The same money already known from another source (statement or another alert)."""
    return session.scalars(
        select(Credit).where(Credit.org_id == org_id, Credit.reference_norm == ref_norm)
    ).first()


def ingest(
    session: Session,
    src: AlertSource,
    *,
    sender: str,
    body: str,
    received_at: datetime | None = None,
    subject: str | None = None,
    raw: bytes | None = None,
) -> IngestResult:
    received_at = received_at or datetime.now(UTC)
    now = datetime.now(UTC)
    src.last_seen_at = now
    src.last_message_at = now
    src.stale_notified_at = None

    digest = _digest(sender, subject, body, raw)
    _rec = functools.partial(_record, digest=digest)
    seen = session.scalars(
        select(AlertMessage).where(
            AlertMessage.source_id == src.source_id, AlertMessage.body_sha256 == digest
        )
    ).first()
    if seen:
        return IngestResult(seen, None)  # forwarders retry; the same alert is processed once

    if not _sender_allowed(src, sender):
        return IngestResult(
            _rec(
                session,
                src,
                sender,
                subject,
                body,
                received_at,
                "rejected",
                "sender is not on this source's allowed list",
            ),
            None,
        )
    try:
        p = parse(f"{subject or ''}\n{body}", sender, src.template)
    except NotACredit as exc:
        return IngestResult(
            _rec(session, src, sender, subject, body, received_at, "ignored", exc.reason), None
        )
    parsed = {
        "amount_minor": p.amount_minor,
        "currency": p.currency,
        "reference": p.reference,
        "payer": p.payer,
        "template": p.template,
        "occurred_at": p.occurred_at.isoformat() if p.occurred_at else None,
    }
    if not p.reference:
        # without a reference we cannot tell this money apart from a statement line later,
        # so it is kept for finance to see but never becomes a credit (no double counting)
        return IngestResult(
            _rec(
                session,
                src,
                sender,
                subject,
                body,
                received_at,
                "unparsed",
                "no transaction reference in the alert",
                parsed,
            ),
            None,
        )
    ref_norm = normalise_reference(p.reference) or p.reference
    twin = _twin(session, src.org_id, ref_norm)
    if twin is not None:
        if twin.amount_minor != p.amount_minor or twin.currency != p.currency:
            msg = _rec(
                session,
                src,
                sender,
                subject,
                body,
                received_at,
                "conflict",
                f"reference already known with a different amount "
                f"({twin.amount_minor} {twin.currency})",
                parsed,
                twin.credit_id,
            )
            audit(
                session,
                actor=f"alert:{src.source_id}",
                action="alert.conflict",
                entity_type="credit",
                entity_id=twin.credit_id,
                org_id=src.org_id,
                after={"alert_amount": p.amount_minor, "message_id": str(msg.message_id)},
            )
            return IngestResult(msg, None)
        return IngestResult(
            _rec(
                session,
                src,
                sender,
                subject,
                body,
                received_at,
                "duplicate",
                f"already known from {twin.source}",
                parsed,
                twin.credit_id,
            ),
            twin,
        )

    occurred = p.occurred_at or received_at
    # an alert can't be from the future, and stale alerts are suspicious; use receipt time then
    if occurred > now + timedelta(minutes=10) or occurred < received_at - timedelta(days=7):
        occurred = received_at
    credit_id = session.execute(
        insert(Credit)
        .values(
            credit_id=uuid.uuid4(),
            org_id=src.org_id,
            source="alert",
            provider=src.provider or p.provider,
            provider_txn_id=p.reference,
            reference_norm=ref_norm,
            amount_minor=p.amount_minor,
            currency=p.currency,
            occurred_at=occurred,
            status="success",
            beneficiary=src.account_alias,
            payer=p.payer,
            narrative=body[:1000],
            created_by=f"alert:{src.source_id}",
        )
        .on_conflict_do_nothing(constraint="uq_credit_provider_txn")
        .returning(Credit.credit_id)
    ).scalar()
    if credit_id is None:
        return IngestResult(
            _rec(
                session,
                src,
                sender,
                subject,
                body,
                received_at,
                "duplicate",
                "already known",
                parsed,
            ),
            None,
        )
    msg = _rec(
        session, src, sender, subject, body, received_at, "credited", None, parsed, credit_id
    )
    credit = session.get(Credit, credit_id)
    assert credit is not None
    credit.source_record_id = f"alert:{msg.message_id}"  # link back for the audit trail
    audit(
        session,
        actor=f"alert:{src.source_id}",
        action="credit.from_alert",
        entity_type="credit",
        entity_id=credit_id,
        org_id=src.org_id,
        after={**parsed, "source": src.name},
    )
    return IngestResult(msg, credit)


def ingest_email(session: Session, src: AlertSource, raw: bytes, dkim_verify=None) -> IngestResult:
    """A raw RFC 822 message from the alert mailbox (IMAP poller or inbound-mail webhook)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1]
    subject = str(msg.get("Subject", ""))
    date = msg.get("Date")
    try:
        received = email.utils.parsedate_to_datetime(str(date)) if date else None
    except (TypeError, ValueError):
        received = None
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"[ \t]+", " ", body).strip()
    if src.require_dkim and not (dkim_verify or _dkim_ok)(raw):
        rec = _record(
            session,
            src,
            sender,
            subject,
            body,
            received or datetime.now(UTC),
            "rejected",
            "missing or invalid DKIM signature",
            digest=_digest(sender, subject, body, raw),
        )
        return IngestResult(rec, None)
    return ingest(
        session, src, sender=sender, body=body, received_at=received, subject=subject, raw=raw
    )


def _dkim_ok(raw: bytes) -> bool:
    try:
        import dkim

        return bool(dkim.verify(raw))
    except Exception:  # noqa: BLE001 - any verification failure is a failure
        return False


def poll_imap(session: Session, imap_factory=None) -> int:
    """Fetch unseen messages from the configured alert mailbox and ingest them."""
    import imaplib

    s = get_settings()
    if not (s.alert_imap_host and s.alert_imap_source_id):
        return 0
    src = session.get(AlertSource, uuid.UUID(s.alert_imap_source_id))
    if src is None or not src.active or src.kind != "email":
        log.error("POPS_ALERT_IMAP_SOURCE_ID does not name an active email alert source")
        return 0
    box = (imap_factory or imaplib.IMAP4_SSL)(s.alert_imap_host)
    box.login(s.alert_imap_user or "", s.alert_imap_password or "")
    box.select(s.alert_imap_folder)
    _, data = box.search(None, "UNSEEN")
    count = 0
    for raw_num in (data[0] or b"").split():
        num = raw_num.decode() if isinstance(raw_num, bytes) else str(raw_num)
        _, parts = box.fetch(num, "(RFC822)")
        raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
        if raw:
            ingest_email(session, src, raw)
            count += 1
        box.store(num, "+FLAGS", "\\Seen")
    box.logout()
    src.last_seen_at = datetime.now(UTC)  # a successful poll is the mailbox's heartbeat
    return count


def reconcile_with_statement(session: Session, statement_credit_values: dict) -> str | None:
    """Called by statement import before inserting a line. Returns how the line was handled:
    'upgraded' (an alert credit already holds this money: it is now statement-confirmed),
    'disputed' (the alert disagrees with the bank: the alert credit is frozen), or None.
    """
    v = statement_credit_values
    q = select(Credit).where(Credit.org_id == v["org_id"], Credit.source == "alert")
    if v["reference_norm"]:
        q = q.where(Credit.reference_norm == v["reference_norm"])
    elif len(v.get("narrative_norm") or "") >= 6:
        # no bank reference on the line: the alert's reference may sit in the narrative
        q = q.where(
            func.length(Credit.reference_norm) >= 6,
            func.strpos(literal(v["narrative_norm"]), Credit.reference_norm) > 0,
            Credit.amount_minor == v["amount_minor"],
        )
    else:
        return None
    twin = session.scalars(q.with_for_update()).first()
    if twin is None:
        return None
    if twin.amount_minor == v["amount_minor"] and twin.currency == v["currency"]:
        twin.source = "statement"
        twin.import_id = v["import_id"]
        audit(
            session,
            actor=v["created_by"],
            action="credit.confirmed_by_statement",
            entity_type="credit",
            entity_id=twin.credit_id,
            org_id=twin.org_id,
            after={"import_id": str(v["import_id"])},
        )
        for claim in session.scalars(
            select(Claim).where(Claim.candidate_credit_id == twin.credit_id)
        ):
            claim.evidence_tier = "E3"
        return "upgraded"
    twin.status = "disputed"  # the database refuses to allocate a credit that is not success
    # free the reference so the bank's own line is recorded and matched normally
    twin.provider_txn_id = f"{twin.provider_txn_id}#alert-disputed"
    twin.reference_norm = f"{twin.reference_norm}#ALERTDISPUTED"
    allocated = session.scalars(
        select(Allocation.claim_id).where(
            Allocation.credit_id == twin.credit_id, Allocation.kind == "allocation"
        )
    ).all()
    session.add(
        OutboxEvent(
            org_id=twin.org_id,
            event_type="ops.alert_credit_disputed",
            event_key=f"disputed:{twin.credit_id}",
            payload={
                "text": "A bank statement contradicts an SMS/email alert credit "
                f"({twin.amount_minor} vs {v['amount_minor']} {twin.currency}). The "
                "alert credit is frozen. Possible spoofed alert"
                + (
                    f"; {len(allocated)} allocation(s) on it need review and reversal."
                    if allocated
                    else "."
                ),
                "credit_id": str(twin.credit_id),
                "claims_with_allocations": [str(c) for c in allocated if c],
            },
        )
    )
    audit(
        session,
        actor=v["created_by"],
        action="credit.alert_disputed_by_statement",
        entity_type="credit",
        entity_id=twin.credit_id,
        org_id=twin.org_id,
        before={"amount_minor": twin.amount_minor},
        after={"statement_amount_minor": v["amount_minor"]},
    )
    for claim in session.scalars(
        select(Claim).where(Claim.candidate_credit_id == twin.credit_id).with_for_update()
    ):
        claim.candidate_credit_id = None
        reasons = [*claim.reason_codes, "CREDIT_DISPUTED"]
        if claim.state == ClaimState.FINANCIALLY_CONFIRMED:
            transition(
                session, claim, ClaimState.PENDING_REVIEW, actor=v["created_by"], reasons=reasons
            )
        else:
            claim.reason_codes = reasons
    return "disputed"


def check_stale_sources(session: Session) -> int:
    """Notify admins once when a forwarder stops checking in (phone off, app killed)."""
    s = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=s.alert_source_stale_minutes)
    stale = session.scalars(
        select(AlertSource).where(
            AlertSource.active.is_(True),
            AlertSource.last_seen_at < cutoff,
            AlertSource.stale_notified_at.is_(None),
        )
    ).all()
    for src in stale:
        src.stale_notified_at = datetime.now(UTC)
        session.add(
            OutboxEvent(
                org_id=src.org_id,
                event_type="ops.alert_source_stale",
                event_key=f"stale:{src.source_id}:{src.last_seen_at}",
                payload={
                    "text": f"POPs alert source '{src.name}' has not checked in since "
                    f"{src.last_seen_at:%Y-%m-%d %H:%M} UTC. Payments may not be "
                    "confirmed automatically until it is back.",
                    "source_id": str(src.source_id),
                },
            )
        )
    obs.STALE_ALERT_SOURCES.set(len(stale))
    return len(stale)
