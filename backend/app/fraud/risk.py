"""Fake-POP risk scoring and admin escalation.

Every claim gets a 0-100 risk score built from independent, explainable signals: reuse of a
receipt, a bank record that contradicts the receipt, editing or AI-generation traces in the file,
image forensics, the model's own observations, payer history and velocity. Each signal carries a
plain-language reason for the admin.

What the score can do: mark a claim suspicious (shown to reviewers) or a suspected fake (admin
alert, confirmation blocked until an admin decides). What it cannot do: approve anything, or
reject anything on its own. People decide, and payers never see an accusation (blueprint 4.5).
"""

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.audit import audit
from app.extraction import templates
from app.extraction.agreement import FinalField
from app.guardrails.rules import active_rules, reason_code
from app.intake.service import submission_evidence
from app.models import (
    ApprovalTier,
    Claim,
    ClaimState,
    Credit,
    Decision,
    Evidence,
    FraudAlert,
    Obligation,
    OutboxEvent,
)
from app.policy.engine import Policy
from app.reconcile.matching import Candidate, ClaimFacts

EDITORS = re.compile(
    r"photoshop|gimp|canva|picsart|snapseed|lightroom|pixlr|paint\.net|affinity|fotor|photopea|"
    r"inpaint|facetune|pdfescape|sejda|pdf\s*editor|pdf-xchange\s*editor|foxit\s*phantom|"
    r"nitro\s*pro|ilovepdf|smallpdf|pdfelement|libreoffice\s*draw|inkscape|word|excel",
    re.I,
)
GENERATORS = re.compile(
    r"dall[\s·-]?e|midjourney|stable\s*diffusion|sdxl|firefly|openai|imagen|gpt-image|"
    r"c2pa|comfyui|automatic1111|flux|ideogram|leonardo|sampler:|cfg scale|negative prompt",
    re.I,
)
SUSPECTED_FAKE = "SUSPECTED_FAKE"
DUPLICATE_REASONS = {
    "DUPLICATE_FILE",
    "DUPLICATE_NEAR_FILE",
    "DUPLICATE_REFERENCE",
    "REFERENCE_ALREADY_CONSUMED",
}
# signals about the file itself; halved when the money is independently confirmed
FORENSIC_ONLY = {
    "EDITING_SOFTWARE",
    "PASTED_TEXT",
    "ELA_LOCAL_EDIT",
    "AI_SUSPECTS_EDITING",
    "READERS_DISAGREE",
    "PDF_EDITED_AFTER_CREATION",
    "TYPED_DETAILS_DIFFER",
    "VELOCITY",
    "DATE_BEFORE_INVOICE",
    "REFERENCE_FORMAT_INVALID",
    "EDITABLE_DOCUMENT",
}


@dataclass
class Signal:
    code: str
    weight: int
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    score: int
    level: str  # low | medium | high
    signals: list[Signal]

    def as_json(self) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.signals]


def _meta_text(ev: Evidence | None) -> dict[str, str]:
    """Flatten the metadata fields where editors and generators leave their names."""
    if ev is None:
        return {}
    m = ev.meta or {}
    out: dict[str, str] = {}
    for k, v in (m.get("exif") or {}).items():
        out[f"exif.{k}"] = str(v)
    for k, v in (m.get("image_text") or {}).items():
        out[f"png.{k}"] = str(v)
    for k, v in (m.get("pdf") or {}).items():
        if isinstance(v, str):
            out[f"pdf.{k}"] = v
    return out


def _file_signals(ev: Evidence, add: Any) -> None:
    """Signals read from one file's own metadata and forensics."""
    meta = _meta_text(ev)
    editors = {k: v for k, v in meta.items() if EDITORS.search(v)}
    if editors:
        add(
            "EDITING_SOFTWARE",
            "The file's metadata names image or PDF editing software.",
            metadata=editors,
        )
    generated = {
        k: v[:120] for k, v in meta.items() if GENERATORS.search(v) or k == "png.parameters"
    }
    if generated:
        add(
            "AI_GENERATED_MARKERS",
            "The file carries metadata written by an AI image generator.",
            metadata=generated,
        )
    pdf = (ev.meta or {}).get("pdf", {})
    if pdf.get("eof_markers", 1) > 1 or (
        pdf.get("creationDate")
        and pdf.get("modDate")
        and pdf["creationDate"][:16] != pdf["modDate"][:16]
    ):
        add(
            "PDF_EDITED_AFTER_CREATION",
            "The PDF was modified after it was first created.",
            eof_markers=pdf.get("eof_markers"),
            created=pdf.get("creationDate"),
            modified=pdf.get("modDate"),
        )
    if ev.content_type.endswith("wordprocessingml.document"):
        add(
            "EDITABLE_DOCUMENT",
            "A Word document is easy to type up or edit; banks and mobile "
            "money apps do not issue receipts as Word files.",
            properties=(ev.meta or {}).get("docx", {}),
        )
    ela = (ev.meta or {}).get("ela")
    if ela and ela.get("suspicious"):
        add(
            "ELA_LOCAL_EDIT",
            "Image forensics found a region compressed differently from the "
            "rest of the photo, as happens when text is pasted in.",
            score=ela.get("score"),
        )
    if ev.content_type == "text/plain":
        add(
            "PASTED_TEXT",
            "The proof is pasted text, which anyone can type; it is only as good as "
            "the bank record it matches.",
            chars=(ev.meta or {}).get("pasted_text", {}).get("chars"),
        )


def assess(
    session: Session,
    claim: Claim,
    obligation: Obligation,
    policy: Policy,
    facts: ClaimFacts,
    candidates: list[Candidate],
    fields: dict[str, FinalField] | None = None,
) -> Assessment:
    cfg = policy.config
    w = cfg.fraud_weight
    signals: list[Signal] = []
    reasons = set(claim.reason_codes)
    evs = submission_evidence(session, claim.claim_id)

    def add(code: str, reason: str, **evidence: Any) -> None:
        if w(code) > 0 and all(s.code != code for s in signals):
            signals.append(Signal(code, w(code), reason, evidence))

    # 1. the same receipt or transaction used before
    reused = sorted(DUPLICATE_REASONS & reasons)
    if reused:
        add(
            "REUSED_RECEIPT",
            "This receipt or its transaction reference was already submitted or credited.",
            reasons=reused,
        )

    # 2. our bank record contradicts the receipt (the strongest sign of an edited receipt)
    for c in candidates:
        if c.reference_kind != "txn_id":
            continue
        gates = {g.name: g for g in c.gates}
        if not gates["amount"].passed and facts.amount_minor is not None:
            add(
                "AMOUNT_EDIT_SUSPECTED",
                "A bank record with this exact transaction reference "
                "shows a different amount than the receipt.",
                detail=gates["amount"].detail,
                credit_id=str(c.credit_id),
            )
        if "date_window" in gates and not gates["date_window"].passed and facts.occurred_at:
            add(
                "DATE_EDIT_SUSPECTED",
                "The bank record with this reference has a different date than the receipt.",
                detail=gates["date_window"].detail,
            )
        if not gates["beneficiary"].passed:
            add(
                "WRONG_RECIPIENT",
                "The transaction with this reference was paid to an account that is not ours.",
                detail=gates["beneficiary"].detail,
            )
    if "NO_CREDIT_FOUND" in reasons:
        add(
            "NO_CREDIT_AFTER_STATEMENT",
            "Bank statements covering the receipt's date have been "
            "imported and no matching payment arrived.",
        )

    # 3. traces left in the files by editors and image generators (every file of the submission)
    for ev in evs:
        _file_signals(ev, add)
    if "PROOFS_DISAGREE" in reasons:
        add(
            "PROOFS_DISAGREE",
            "Two proofs sent for this one payment show different amounts, dates or references.",
        )

    # 4. content checks
    tx = fields.get("transaction_id") if fields else None
    provider = fields.get("provider") if fields else None
    tpl = templates.template_for(provider.normalised if provider else None)
    if (
        tx
        and tx.raw
        and tpl
        and tpl.txn_patterns
        and not any(re.search(p, tx.raw, re.I) for p in tpl.txn_patterns)
    ):
        add(
            "REFERENCE_FORMAT_INVALID",
            f"The reference does not match the usual {tpl.provider} format.",
            reference=tx.raw[:40],
        )
    notes = fields.get("tampering_notes") if fields else None
    if notes and notes.raw and notes.raw.strip().lower() not in ("none", "no", "n/a", "null", ""):
        add(
            "AI_SUSPECTS_EDITING",
            "The vision model noticed possible editing.",
            notes=notes.raw[:300],
        )
    if fields:
        for name in ("amount", "transaction_id"):
            f = fields.get(name)
            vals = {v for v in (f.readings.values() if f else []) if v}
            if len(vals) > 1:
                add(
                    "READERS_DISAGREE",
                    "The two independent readers read different values "
                    "for a critical field, which can happen when text was overlaid.",
                    field=name,
                )
    elif "EXTRACTION_UNCONFIRMED" in reasons and any(
        s.get("code") == "READERS_DISAGREE" for s in claim.risk_signals or []
    ):
        add("READERS_DISAGREE", "The two readers disagreed on a critical field.")
    if "RECEIPT_STATUS_NOT_SUCCESS" in reasons:
        add("STATUS_NOT_SUCCESS", "The receipt itself shows the payment as not completed.")
    if reasons & {"TYPED_AMOUNT_DISAGREES", "TYPED_REFERENCE_DISAGREES"}:
        add("TYPED_DETAILS_DIFFER", "The details the payer typed differ from the receipt.")
    now = datetime.now(UTC)
    if facts.occurred_at and min(
        [facts.occurred_at, *facts.occurred_alternatives]
    ) > now + timedelta(hours=2):
        add(
            "FUTURE_DATE", "The receipt is dated in the future.", date=facts.occurred_at.isoformat()
        )
    if (
        facts.occurred_at
        and obligation.created_at
        and max([facts.occurred_at, *facts.occurred_alternatives])
        < obligation.created_at - timedelta(days=2)
    ):
        add("DATE_BEFORE_INVOICE", "The receipt is dated before the invoice existed.")
    blocked = {
        reason_code(r) for r in active_rules(session, claim.org_id) if r.kind == "blocked_text"
    }
    if blocked & reasons:
        add(
            "PROMPT_INJECTION_TEXT",
            "The receipt contains text aimed at the AI or the reviewer.",
            rules=sorted(blocked & reasons),
        )

    # 5. payer behaviour
    history = session.scalar(
        select(func.count())
        .select_from(Claim)
        .outerjoin(FraudAlert, FraudAlert.claim_id == Claim.claim_id)
        .outerjoin(Decision, Decision.claim_id == Claim.claim_id)
        .where(
            Claim.org_id == claim.org_id,
            Claim.claimant_ref == claim.claimant_ref,
            Claim.claim_id != claim.claim_id,
            (FraudAlert.status == "confirmed_fake")
            | (
                (Decision.outcome == ClaimState.REJECTED)
                & Decision.reason_codes.has_any(
                    pg_array(["TAMPERED_DOCUMENT", "PAYMENT_NOT_RECEIVED", "DUPLICATE_CONFIRMED"])
                )
            ),
        )
    )
    if history:
        add(
            "PAYER_HISTORY",
            "This payer has earlier claims rejected as fake, duplicate or never paid.",
            count=int(history),
        )
    recent = session.scalar(
        select(func.count())
        .select_from(Claim)
        .where(
            Claim.org_id == claim.org_id,
            Claim.claimant_ref == claim.claimant_ref,
            Claim.created_at > now - timedelta(days=1),
        )
    )
    if recent and recent > cfg.velocity_claims_per_day:
        add("VELOCITY", f"{recent} claims from this payer in 24 hours.", count=int(recent))

    # money confirmed on our side makes file-only doubts matter less (not reuse or generation)
    confirmed = any(c.passed for c in candidates)
    for s in signals:
        if confirmed and s.code in FORENSIC_ONLY:
            s.weight //= 2
            s.evidence["dampened"] = "payment independently confirmed by a bank record"
    score = min(100, sum(s.weight for s in signals))
    level = (
        "high"
        if score >= cfg.fraud_high_score
        else "medium"
        if score >= cfg.fraud_medium_score
        else "low"
    )
    signals.sort(key=lambda s: -s.weight)
    return Assessment(score, level, signals)


def apply(session: Session, claim: Claim, a: Assessment, actor: str) -> FraudAlert | None:
    """Store the assessment on the claim and open, update or reopen the admin alert."""
    claim.risk_score = a.score
    claim.risk_level = a.level
    claim.risk_signals = a.as_json()
    reasons = [r for r in claim.reason_codes if r != SUSPECTED_FAKE]
    alert = session.scalars(select(FraudAlert).where(FraudAlert.claim_id == claim.claim_id)).first()

    if a.level == "low" and (alert is None or alert.status != "open"):
        claim.reason_codes = reasons
        return alert
    codes = {s.code for s in a.signals}
    notify = False
    if alert is None:
        alert = FraudAlert(
            org_id=claim.org_id,
            claim_id=claim.claim_id,
            level=a.level,
            score=a.score,
            signals=a.as_json(),
            status="open",
        )
        session.add(alert)
        session.flush()
        notify = a.level == "high"
        audit(
            session,
            actor=actor,
            action="fraud.alert_opened",
            entity_type="claim",
            entity_id=claim.claim_id,
            org_id=claim.org_id,
            after={"score": a.score, "level": a.level, "signals": sorted(codes)},
            correlation_id=claim.correlation_id,
        )
    elif alert.status == "open":
        notify = a.level == "high" and alert.level != "high"
        alert.level, alert.score, alert.signals = a.level, a.score, a.as_json()
    elif alert.status == "false_positive":
        cleared = {s["code"] for s in alert.signals}
        if a.level == "high" and not codes <= cleared:
            # new evidence since the admin cleared it: ask again
            alert.status, alert.level, alert.score = "open", a.level, a.score
            alert.signals = a.as_json()
            notify = True
            audit(
                session,
                actor=actor,
                action="fraud.alert_reopened",
                entity_type="claim",
                entity_id=claim.claim_id,
                org_id=claim.org_id,
                after={"new_signals": sorted(codes - cleared)},
                correlation_id=claim.correlation_id,
            )
    if alert.status == "open" and alert.level == "high":
        reasons.append(SUSPECTED_FAKE)
        claim.approval_tier = ApprovalTier.RED
    claim.reason_codes = reasons
    if notify:
        obs.FRAUD_ALERTS.labels(a.level).inc()
        _notify(session, claim, alert)
    return alert


def _notify(session: Session, claim: Claim, alert: FraudAlert) -> None:
    """Admin notification via the outbox. No payer names, numbers or document contents."""
    top = ", ".join(s["code"] for s in alert.signals[:4])
    key = f"fraud:{alert.alert_id}:{alert.score}:{alert.status}"
    if session.scalars(select(OutboxEvent).where(OutboxEvent.event_key == key)).first():
        return
    session.add(
        OutboxEvent(
            org_id=claim.org_id,
            event_type="fraud.suspected",
            event_key=key,
            payload={
                "text": f"Suspected fake proof of payment (risk {alert.score}/100): {top}. "
                f"Review claim {claim.claim_id} in the POPs console.",
                "alert_id": str(alert.alert_id),
                "claim_id": str(claim.claim_id),
                "score": alert.score,
                "level": alert.level,
                "signals": [s["code"] for s in alert.signals],
            },
        )
    )


def open_high_alert(session: Session, claim_id: uuid.UUID) -> FraudAlert | None:
    return session.scalars(
        select(FraudAlert).where(
            FraudAlert.claim_id == claim_id,
            FraudAlert.status == "open",
            FraudAlert.level == "high",
        )
    ).first()


def coverage_end(session: Session, org_id: uuid.UUID, accounts: list[str]) -> datetime | None:
    """Latest statement-line time we hold for our receiving accounts."""
    return session.scalar(
        select(func.max(Credit.occurred_at)).where(
            Credit.org_id == org_id,
            Credit.source == "statement",
            Credit.beneficiary.in_(accounts),
        )
    )
