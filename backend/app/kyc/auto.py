"""Rules-based identity verification of clear cases, plus the sampled human audit of it.

Same contract as `review/auto.py`: the AI never verifies. The readers and the face matcher produce
facts; this module is a fixed list of checks over those facts. A verification is approved here
only when the document is of an accepted type and country, two independent sources agree on the
critical fields (or a valid MRZ does), it is not expired, the face matches, the selfie looks live,
nothing about the document or its images looks tampered or reused, and no other person is using
the same ID. Anything else goes to a reviewer, exactly as before.

It never rejects. Off by default (policy `kyc_auto_verify_mode`), `shadow` records what it would
have done without doing it, and `POPS_KYC_AUTO_DISABLED` is a kill switch over every organisation.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal
from app.models import KycDecision, KycState, KycVerification
from app.policy.engine import PolicyConfig, load_policy

log = logging.getLogger(__name__)

AUTO_ACTOR = "rule:kyc-auto"
VERDICT_ACTOR_TYPE = "audit"
VERDICTS = {"ok": "KYC_AUDIT_OK", "overturned": "KYC_AUDIT_OVERTURNED"}


class KycError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class Inputs:
    """The facts the rules look at, so replay and tests can ask the same questions."""

    subject_type: str
    doc_type: str | None
    issuing_country: str | None
    mrz_valid: bool | None  # None = no MRZ found
    ocr_agreement: float | None
    conflicts: list[str] = field(default_factory=list)
    days_to_expiry: int | None = None
    number_format_ok: bool | None = None  # None = no opinion
    face_score: float | None = None
    liveness_score: float | None = None
    tamper_score: float | None = None
    integrity_flags: list[str] = field(default_factory=list)
    readers_ok: bool = True


def rule_failures(cfg: PolicyConfig, i: Inputs, *, kill_switch: bool = False) -> list[str]:
    """Every rule that stops automatic verification (empty means it may be verified)."""
    out: list[str] = []
    if kill_switch:
        out.append("KILL_SWITCH")
    if cfg.kyc_auto_verify_mode == "off":
        out.append("DISABLED")
    if i.subject_type not in cfg.kyc_auto_verify_subjects:
        out.append("SUBJECT_NOT_ENABLED")
    if not i.readers_ok:
        out.append("READERS_UNAVAILABLE")
    if i.doc_type is None:
        out.append("DOC_TYPE_UNKNOWN")
    elif i.doc_type not in cfg.kyc_allowed_doc_types:
        out.append("DOC_TYPE_NOT_ALLOWED")
    if i.issuing_country is None:
        out.append("COUNTRY_UNKNOWN")
    elif i.issuing_country not in cfg.kyc_allowed_countries:
        out.append("COUNTRY_NOT_ALLOWED")
    if i.mrz_valid is False:
        out.append("MRZ_CHECKSUM_FAIL")
    elif i.mrz_valid is None and (cfg.kyc_require_mrz or i.doc_type == "passport"):
        out.append("MRZ_REQUIRED")  # a passport always carries one; policy may demand it of IDs
    if i.ocr_agreement is None or i.ocr_agreement < cfg.kyc_min_ocr_agreement:
        out.append("LOW_OCR_AGREEMENT")
    if i.conflicts:
        out.append("FIELD_CONFLICT")
    if i.days_to_expiry is None:
        out.append("EXPIRY_UNKNOWN")
    elif i.days_to_expiry < 0:
        out.append("DOC_EXPIRED")
    elif i.days_to_expiry < cfg.kyc_min_expiry_days:
        out.append("DOC_EXPIRES_SOON")
    if i.number_format_ok is False:
        out.append("NUMBER_FORMAT_INVALID")
    if i.face_score is None:
        out.append("FACE_UNAVAILABLE")
    elif i.face_score < cfg.kyc_min_face_score:
        out.append("FACE_SCORE_LOW")
    if i.liveness_score is None:
        out.append("LIVENESS_UNAVAILABLE")
    elif i.liveness_score < cfg.kyc_min_liveness:
        out.append("LIVENESS_LOW")
    if cfg.tamper_enabled:
        if i.tamper_score is None:
            out.append("TAMPER_CHECK_SKIPPED")
        elif i.tamper_score >= cfg.tamper_review_score:
            out.append("DOC_TAMPER_SUSPECTED")
    out.extend(f for f in i.integrity_flags if f not in out)
    return out


def maybe_auto_verify(
    session: Session, kyc: KycVerification, inputs: Inputs, snapshot: dict[str, Any]
) -> bool:
    """Apply the rules to a processed verification. Returns True when it was verified.

    Mode `on`: verify when nothing blocks it. Mode `shadow`: record what would have happened and
    leave the verification for a person. Mode `off`: nothing recorded here."""
    from app.kyc import service  # local import: service imports this module

    policy = load_policy(session, kyc.org_id)
    cfg = policy.config
    failures = rule_failures(cfg, inputs, kill_switch=get_settings().kyc_auto_disabled)
    kyc.reason_codes = failures
    if cfg.kyc_auto_verify_mode == "off" or get_settings().kyc_auto_disabled:
        return False
    if cfg.kyc_auto_verify_mode == "shadow":
        service.record_decision(
            session,
            kyc,
            "SHADOW_WOULD_REVIEW" if failures else "SHADOW_WOULD_VERIFY",
            failures,
            policy.version,
            "rule",
            AUTO_ACTOR,
            {**snapshot, "shadow": True},
        )
        return False
    if failures:
        log.info("kyc %s left for a person: %s", kyc.kyc_id, ", ".join(failures))
        return False
    service.set_verified(
        session,
        kyc,
        cfg,
        policy.version,
        "rule",
        AUTO_ACTOR,
        {
            **snapshot,
            "auto": True,
            "policy": {
                "min_face_score": cfg.kyc_min_face_score,
                "min_liveness": cfg.kyc_min_liveness,
                "min_ocr_agreement": cfg.kyc_min_ocr_agreement,
            },
        },
        note="verified by rule: document, agreement, face and liveness checks all passed",
        reasons=["AUTO_VERIFIED"],
    )
    return True


# --- sampled human audit ------------------------------------------------------------------------


def is_sampled(kyc_id: uuid.UUID, pct: int) -> bool:
    """Deterministic: the same verification is always in or out of the sample."""
    digest = hashlib.sha256(str(kyc_id).encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < pct


def list_auto_verified(session: Session, org_id: uuid.UUID, limit: int = 200) -> dict[str, Any]:
    cfg = load_policy(session, org_id).config
    verified = session.scalars(
        select(KycDecision)
        .where(
            KycDecision.org_id == org_id,
            KycDecision.actor_id == AUTO_ACTOR,
            KycDecision.outcome == KycState.VERIFIED,
        )
        .order_by(KycDecision.created_at.desc())
        .limit(1000)
    ).all()
    ids = [d.kyc_id for d in verified]
    verdicts: dict[uuid.UUID, KycDecision] = {}
    subjects: dict[uuid.UUID, KycVerification] = {}
    if ids:
        for v in session.scalars(
            select(KycDecision)
            .where(KycDecision.kyc_id.in_(ids), KycDecision.actor_type == VERDICT_ACTOR_TYPE)
            .order_by(KycDecision.created_at)
        ):
            verdicts[v.kyc_id] = v
        subjects = {
            k.kyc_id: k
            for k in session.scalars(select(KycVerification).where(KycVerification.kyc_id.in_(ids)))
        }
    rows, sampled_n, audited, overturned = [], 0, 0, 0
    for d in verified:
        sampled = is_sampled(d.kyc_id, cfg.kyc_audit_pct)
        verdict = verdicts.get(d.kyc_id)
        sampled_n += int(sampled)
        audited += int(verdict is not None)
        overturned += int(verdict is not None and verdict.outcome == VERDICTS["overturned"])
        k = subjects.get(d.kyc_id)
        rows.append(
            {
                "kyc_id": str(d.kyc_id),
                "verified_at": d.created_at.isoformat(),
                "subject_type": k.subject_type if k else None,
                "subject_ref": k.subject_ref if k else None,
                "doc_type": k.doc_type if k else None,
                "state": k.state if k else None,
                "face_score": k.face_score if k else None,
                "sampled": sampled,
                "verdict": (
                    {
                        "outcome": verdict.outcome,
                        "by": verdict.actor_id,
                        "at": verdict.created_at.isoformat(),
                        "note": verdict.note,
                    }
                    if verdict
                    else None
                ),
            }
        )
    return {
        "mode": cfg.kyc_auto_verify_mode,
        "enabled": cfg.kyc_auto_verify_mode == "on" and not get_settings().kyc_auto_disabled,
        "audit_pct": cfg.kyc_audit_pct,
        "summary": {
            "verified": len(verified),
            "sampled": sampled_n,
            "audited": audited,
            "overturned": overturned,
            "overturn_rate": round(overturned / audited, 4) if audited else None,
        },
        "verifications": rows[:limit],
    }


def record_verdict(
    session: Session, p: Principal, kyc_id: uuid.UUID, verdict: str, note: str | None
) -> KycDecision:
    """A reviewer's judgement on an automatic verification. Overturning does not revoke it:
    revoke separately (decide -> REJECTED), so every correction has its own audit trail."""
    from app.kyc import service

    if verdict not in VERDICTS:
        raise KycError("BAD_VERDICT", f"verdict must be one of {sorted(VERDICTS)}", 422)
    kyc = session.scalars(
        select(KycVerification)
        .where(KycVerification.kyc_id == kyc_id, KycVerification.org_id == p.org_id)
        .with_for_update()
    ).first()
    if kyc is None:
        raise KycError("NOT_FOUND", "verification not found", 404)
    if service.is_own_identity(p, kyc):
        raise KycError("SELF_REVIEW", "you cannot review your own identity check", 403)
    auto = session.scalars(
        select(KycDecision.decision_id).where(
            KycDecision.kyc_id == kyc_id,
            KycDecision.actor_id == AUTO_ACTOR,
            KycDecision.outcome == KycState.VERIFIED,
        )
    ).first()
    if auto is None:
        raise KycError("NOT_AUTO_VERIFIED", "this was not verified automatically", 409)
    if session.scalars(
        select(KycDecision.decision_id).where(
            KycDecision.kyc_id == kyc_id, KycDecision.actor_type == VERDICT_ACTOR_TYPE
        )
    ).first():
        raise KycError("ALREADY_AUDITED", "this verification already has a verdict", 409)
    policy = load_policy(session, kyc.org_id)
    decision = service.record_decision(
        session,
        kyc,
        VERDICTS[verdict],
        [],
        policy.version,
        VERDICT_ACTOR_TYPE,
        p.actor,
        {"audited_at": datetime.now(UTC).isoformat()},
        note,
    )
    audit(
        session,
        actor=p.actor,
        action=f"kyc.audit_{verdict}",
        entity_type="kyc",
        entity_id=kyc_id,
        org_id=p.org_id,
        after={"note": note},
        correlation_id=kyc.correlation_id,
    )
    return decision


def validity_end(cfg: PolicyConfig, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(days=cfg.kyc_validity_days)
