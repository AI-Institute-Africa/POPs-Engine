"""Claim verification pipeline (blueprint 4.1). Review-only: it never allocates money.

Each run is recorded with pipeline, policy, extractor and guardrail versions so the outcome can be
replayed. Steps that fail or are unsure route the claim to a human (fail closed, blueprint 11.2).
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, func, literal, or_, select, type_coerce
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.orm import Session

from app.claims.facts import FACT_FLAGS, build_facts, guardrail_fields
from app.claims.service import record_decision, transition
from app.claims.states import REVIEWABLE, TRANSITIONS
from app.core import observability as obs
from app.core.audit import audit
from app.core.config import get_settings
from app.extraction import preprocess, tamper
from app.extraction.readers.base import Reader
from app.extraction.service import build_readers, extract
from app.fraud import risk
from app.fraud.forensics import ela
from app.guardrails.rules import active_rules, evaluate_fields, plan_readers
from app.intake.files import prepare
from app.intake.storage import get_storage
from app.models import (
    ApprovalTier,
    Claim,
    ClaimState,
    ConsumedReference,
    Credit,
    Evidence,
    EvidenceTier,
    ExtractedField,
    Obligation,
    VerificationRun,
)
from app.policy.engine import Policy, approval_tier, evidence_tier_for_source, load_policy
from app.reconcile.matching import Candidate, find_candidates

log = logging.getLogger(__name__)

PIPELINE_VERSION = "pipeline-1.1"
ACTOR = "rule:pipeline"

NEEDS_INFO = {"CAPTURE_TOO_SMALL", "CAPTURE_BLURRY", "UNREADABLE", "MISSING_AMOUNT"}
DUPLICATE = {
    "DUPLICATE_FILE",
    "DUPLICATE_NEAR_FILE",
    "DUPLICATE_REFERENCE",
    "REFERENCE_ALREADY_CONSUMED",
}
# layer 6 risk flags: they escalate to a reviewer and can never approve (blueprint 8)
TAMPER = {"TAMPER_SUSPECTED"}
TERMINAL_OR_DONE = {ClaimState.REJECTED, ClaimState.REVERSED_OR_REFUNDED}
# reasons that do not, on their own, stop a readable claim from waiting for its credit
SOFT = {"MISSING_REFERENCE"}


def _hamming(a: object, b: int):
    # bit_count over the XOR of two BIGINT hashes, computed inside Postgres (PG14+)
    xor = type_coerce(a, BigInteger).op("#")(literal(b, BigInteger))
    return func.bit_count(func.cast(xor, BIT(64)))


def _file_duplicates(
    session: Session, claim: Claim, ev: Evidence, policy: Policy
) -> tuple[list[str], set[uuid.UUID]]:
    """Exact and perceptual duplicates across ALL organisations (cross-org registry, 5.9)."""
    reasons: list[str] = []
    others: set[uuid.UUID] = set()
    exact = session.scalars(
        select(Evidence.claim_id).where(
            Evidence.sha256 == ev.sha256, Evidence.claim_id != claim.claim_id
        )
    ).all()
    if exact:
        reasons.append("DUPLICATE_FILE")
        others.update(exact)
    if ev.phash_int is not None:
        near = (
            set(
                session.scalars(
                    select(Evidence.claim_id).where(
                        Evidence.claim_id != claim.claim_id,
                        Evidence.phash_int.is_not(None),
                        _hamming(Evidence.phash_int, ev.phash_int)
                        <= policy.config.phash_duplicate_distance,
                    )
                ).all()
            )
            - others
        )
        if near:
            reasons.append("DUPLICATE_NEAR_FILE")
            others.update(near)
    return reasons, others


def _reference_duplicates(
    session: Session, claim: Claim, reference: str | None
) -> tuple[list[str], set[uuid.UUID]]:
    if not reference:
        return [], set()
    reasons: list[str] = []
    if session.get(ConsumedReference, f"ref:{reference}"):
        reasons.append("REFERENCE_ALREADY_CONSUMED")
    declared_norm = func.upper(
        func.regexp_replace(Claim.declared_reference, "[^A-Za-z0-9]", "", "g")
    )
    others = set(
        session.scalars(
            select(Claim.claim_id)
            .outerjoin(
                ExtractedField,
                (ExtractedField.claim_id == Claim.claim_id)
                & (ExtractedField.name == "transaction_id"),
            )
            .where(
                Claim.claim_id != claim.claim_id,
                Claim.state.not_in([s.value for s in TERMINAL_OR_DONE]),
                or_(ExtractedField.normalised_value == reference, declared_norm == reference),
            )
        ).all()
    )
    if others:
        reasons.append("DUPLICATE_REFERENCE")
    return reasons, others


def _hold_others(session: Session, ids: set[uuid.UUID], reason: str) -> None:
    for other in session.scalars(select(Claim).where(Claim.claim_id.in_(ids)).with_for_update()):
        if other.state in REVIEWABLE and other.state != ClaimState.DUPLICATE_HOLD:
            reasons = sorted({*other.reason_codes, reason} - {"AWAITING_CREDIT"})
            other.approval_tier = ApprovalTier.RED
            transition(session, other, ClaimState.DUPLICATE_HOLD, actor=ACTOR, reasons=reasons)
            # both claims share the reuse: score the earlier one too so an admin sees the pair
            # (found by the replay report); which one, if either, is genuine is a human call
            reassess(session, other)


def process_claim(
    session: Session, claim_id: uuid.UUID, readers: list[Reader] | None = None
) -> Claim:
    """Run the pipeline for a RECEIVED claim. The caller commits."""
    started = time.perf_counter()
    claim = session.scalars(select(Claim).where(Claim.claim_id == claim_id).with_for_update()).one()
    if claim.state != ClaimState.RECEIVED:
        return claim  # idempotent: a retried task does nothing
    transition(session, claim, ClaimState.PROCESSING, actor=ACTOR)
    policy = load_policy(session, claim.org_id)
    cfg = policy.config
    settings = get_settings()
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    ev = session.scalars(
        select(Evidence)
        .where(Evidence.claim_id == claim.claim_id)
        .order_by(Evidence.received_at.desc())
    ).first()

    rules = active_rules(session, claim.org_id)
    plan = plan_readers(rules, settings.ollama_url, settings.ollama_model)
    reasons: list[str] = list(plan.reasons)
    checks: list[dict] = []
    guardrail_log = list(plan.applied)
    dup_others: set[uuid.UUID] = set()
    needs_info_codes: set[str] = set()
    final_fields = None
    run = VerificationRun(
        claim_id=claim.claim_id,
        pipeline_version=PIPELINE_VERSION,
        policy_version=policy.version,
        outcome="RUNNING",
    )
    session.add(run)
    session.flush()

    if cfg.enabled_channels is not None and claim.channel not in cfg.enabled_channels:
        reasons.append("CHANNEL_NOT_IN_PILOT")

    if ev is None:
        reasons.append("UNREADABLE")
    else:
        file_reasons, others = _file_duplicates(session, claim, ev, policy)
        reasons += file_reasons
        dup_others |= others
        checks.append({"layer": 1, "check": "duplicate_file", "passed": not file_reasons})

        raw = get_storage().get(ev.object_key)
        prepared = prepare(raw, max_bytes=len(raw) + 1)
        image = prepared.image
        quality = preprocess.assess(image)
        # digital documents (PDF text layer, Word) are read from their text, not their pixels
        problems = (
            []
            if prepared.text.strip()
            else quality.problems(cfg.min_image_side_px, cfg.min_sharpness)
        )
        if prepared.meta.get("pdf", {}).get("page_count", 1) > len(prepared.pages):
            reasons.append("DOCUMENT_TRUNCATED")
        reasons += problems
        checks.append(
            {
                "layer": 1,
                "check": "capture_quality",
                "passed": not problems,
                "sharpness": round(quality.sharpness, 1),
            }
        )

        # tamper forensics (blueprint 8, layer 6): risk flag only, never a decision
        if cfg.tamper_enabled and ev.content_type != "application/pdf":
            forensic = tamper.analyse(image)
            checks.append({"layer": 6, "check": "tamper", **forensic.as_dict()})
            if forensic.score >= cfg.tamper_review_score:
                reasons.append("TAMPER_SUSPECTED")
                obs.TAMPER_FLAGS.inc()

        if ev.content_type == "image/jpeg":
            _forensics(session, ev, image)

        if readers is None:
            readers = build_readers(vlm_allowed=plan.vlm_allowed, hints=plan.hints)
        result = extract(
            prepared.pages,
            raw,
            cfg.critical_fields,
            cfg.single_reader_min_confidence,
            readers,
            text=prepared.text,
            layout=prepared.page_layout,
        )
        run.model_versions = {**result.versions, "guardrails": [r.name for r in rules]}
        for name in result.failures:
            obs.READER_FAILURES.labels(name).inc()
        if result.failures:
            reasons.append("READER_UNAVAILABLE")
        checks.append(
            {
                "layer": 2,
                "check": "extraction",
                "readers": list(result.versions),
                "failures": result.failures,
                "vlm_allowed": plan.vlm_allowed,
                # per-reader normalised readings of the fields that matter, for accuracy reports
                "readings": {
                    name: f.readings
                    for name, f in result.fields.items()
                    if name in ("amount", "transaction_id", "date", "currency")
                },
                "pages": len(prepared.pages),
            }
        )

        verdict = evaluate_fields(
            rules, result.fields, claim.declared_amount_minor, claim.declared_currency
        )
        reasons += verdict.review + verdict.needs_info
        guardrail_log += verdict.applied
        for f in result.fields.values():
            if f.name in verdict.distrusted:
                f.agreed = False  # a guardrail may distrust a reading, never trust one
            session.add(
                ExtractedField(
                    claim_id=claim.claim_id,
                    evidence_id=ev.evidence_id,
                    run_id=run.run_id,
                    name=f.name,
                    raw_value=f.raw,
                    normalised_value=f.normalised,
                    confidence=f.confidence,
                    extractor=f.extractor,
                    extractor_version=f.extractor_version,
                    region=f.region,
                    agreed=f.agreed,
                )
            )
        session.flush()
        if not result.fields and claim.declared_amount_minor is None:
            reasons.append("UNREADABLE")
        needs_info_codes = set(verdict.needs_info)
        final_fields = result.fields
    checks.append({"layer": 6, "check": "guardrails", "applied": guardrail_log})
    for g in guardrail_log:
        obs.GUARDRAIL_HITS.labels(g["effect"]).inc()

    facts, flags = build_facts(session, claim, obligation)
    reasons += flags
    checks.append({"layer": 3, "check": "validation", "flags": flags})

    ref_reasons, ref_others = _reference_duplicates(session, claim, facts.reference)
    reasons += ref_reasons
    dup_others |= ref_others

    candidates = find_candidates(session, facts, obligation, cfg)
    run.candidates = [c.as_dict() for c in candidates]
    best = next((c for c in candidates if c.passed), None)
    checks.append(
        {
            "layer": 5,
            "check": "reconcile",
            "candidates": len(candidates),
            "matched": str(best.credit_id) if best else None,
        }
    )

    reasons = sorted(set(reasons), key=reasons.index)
    if DUPLICATE.intersection(reasons):
        target = ClaimState.DUPLICATE_HOLD
        claim.evidence_tier = EvidenceTier.E1 if ev else EvidenceTier.E0
        _hold_others(
            session,
            dup_others,
            "DUPLICATE_FILE"
            if "DUPLICATE_FILE" in reasons
            else next(r for r in reasons if r in DUPLICATE),
        )
    elif NEEDS_INFO.intersection(reasons) or needs_info_codes.intersection(reasons):
        target = ClaimState.NEEDS_INFORMATION
    elif TAMPER.intersection(reasons):
        # a suspicious document is a reviewer's call: attach the candidate credit as a
        # suggestion, but do not let the match carry the claim to confirmation (blueprint 8)
        if best is not None:
            _attach_credit(session, claim, best)
        else:
            claim.evidence_tier = EvidenceTier.E1 if ev else EvidenceTier.E0
        target = ClaimState.PENDING_REVIEW
    elif best is not None:
        _attach_credit(session, claim, best)
        transition(session, claim, ClaimState.DOCUMENT_VERIFIED, actor=ACTOR)
        target = ClaimState.FINANCIALLY_CONFIRMED
    elif set(reasons) <= SOFT:
        claim.evidence_tier = EvidenceTier.E1
        transition(session, claim, ClaimState.DOCUMENT_VERIFIED, actor=ACTOR)
        reasons.append("AWAITING_CREDIT")
        target = ClaimState.AWAITING_FINANCIAL_CONFIRMATION
    else:
        claim.evidence_tier = EvidenceTier.E1 if ev else EvidenceTier.E0
        target = ClaimState.PENDING_REVIEW

    claim.approval_tier = approval_tier(
        reasons, EvidenceTier(claim.evidence_tier), best is not None
    )
    transition(session, claim, target, actor=ACTOR, reasons=reasons)
    assessment = risk.assess(session, claim, obligation, policy, facts, candidates, final_fields)
    risk.apply(session, claim, assessment, ACTOR)
    checks.append(
        {
            "layer": 6,
            "check": "fraud_risk",
            "score": assessment.score,
            "level": assessment.level,
            "signals": [s.code for s in assessment.signals],
        }
    )
    run.checks = checks
    run.flags = claim.reason_codes
    run.outcome = target
    record_decision(
        session,
        claim,
        outcome=target,
        reasons=claim.reason_codes,
        policy_version=policy.version,
        actor_type="rule",
        actor_id=ACTOR,
        credit_id=claim.candidate_credit_id,
        snapshot={
            "run_id": str(run.run_id),
            "risk": {"score": assessment.score, "level": assessment.level},
            "evidence_sha256": ev.sha256 if ev else None,
            "guardrails": guardrail_log,
            "facts": {
                "amount_minor": facts.amount_minor,
                "currency": facts.currency,
                "reference": facts.reference,
                "occurred_at": facts.occurred_at.isoformat() if facts.occurred_at else None,
            },
        },
    )
    obs.CLAIM_OUTCOMES.labels(target).inc()
    obs.PIPELINE_SECONDS.observe(time.perf_counter() - started)
    return claim


def _forensics(session: Session, ev: Evidence, image) -> None:
    """ELA on JPEG uploads; the heatmap is stored for admins next to the evidence."""
    try:
        result = ela(image)
    except Exception:  # noqa: BLE001 - forensics must never break intake
        log.exception("ELA failed for evidence %s", ev.evidence_id)
        return
    key = ev.object_key.rsplit("/", 1)[0] + "/ela.png"
    get_storage().put(key, result.heatmap_png, "image/png")
    ev.forensics_key = key
    ev.meta = {
        **(ev.meta or {}),
        "ela": {
            "score": result.score,
            "share": result.outlier_share,
            "suspicious": result.suspicious,
        },
    }


def reassess(session: Session, claim: Claim, actor: str = ACTOR) -> None:
    """Re-score fraud risk with the claim's current facts (after corrections or new credits)."""
    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, _ = build_facts(session, claim, obligation)
    candidates = find_candidates(session, facts, obligation, policy.config)
    a = risk.assess(
        session, claim, obligation, policy, facts, candidates, guardrail_fields(session, claim)
    )
    risk.apply(session, claim, a, actor)


def scan_missing_credits(session: Session, org_id: uuid.UUID) -> int:
    """After a statement import: claims whose date is now covered but no money arrived."""
    policy = load_policy(session, org_id)
    end = risk.coverage_end(session, org_id, policy.config.beneficiary_aliases)
    if end is None:
        return 0
    window = timedelta(hours=policy.config.date_window_hours)
    flagged = 0
    claims = session.scalars(
        select(Claim)
        .where(
            Claim.org_id == org_id,
            Claim.candidate_credit_id.is_(None),
            Claim.state.in_(
                [ClaimState.AWAITING_FINANCIAL_CONFIRMATION, ClaimState.PENDING_REVIEW]
            ),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for claim in claims:
        if "NO_CREDIT_FOUND" in claim.reason_codes:
            continue
        obligation = session.get(Obligation, claim.obligation_id)
        assert obligation is not None
        facts, _ = build_facts(session, claim, obligation)
        if facts.occurred_at is None:
            continue
        latest = max([facts.occurred_at, *facts.occurred_alternatives])
        if latest + window < end:
            before = list(claim.reason_codes)
            claim.reason_codes = [*before, "NO_CREDIT_FOUND"]
            audit(
                session,
                actor=ACTOR,
                action="claim.no_credit_found",
                entity_type="claim",
                entity_id=claim.claim_id,
                org_id=org_id,
                before={"reason_codes": before},
                after={"statement_coverage_end": end.isoformat()},
                correlation_id=claim.correlation_id,
            )
            reassess(session, claim)
            flagged += 1
    return flagged


def _attach_credit(session: Session, claim: Claim, best: Candidate) -> None:
    credit = session.get(Credit, best.credit_id)
    assert credit is not None
    claim.candidate_credit_id = credit.credit_id
    claim.evidence_tier = evidence_tier_for_source(credit.source)


def rematch(session: Session, claim: Claim, actor: str = ACTOR) -> Claim:
    """Re-run reconciliation for a claim waiting on money (credit-first matching, blueprint 5.2)."""
    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, _ = build_facts(session, claim, obligation)
    candidates = find_candidates(session, facts, obligation, policy.config)
    best = next((c for c in candidates if c.passed), None)
    if best is None:
        if candidates:
            # a new bank record may contradict the receipt (edited amount or date): re-score
            reassess(session, claim, actor)
        return claim
    _attach_credit(session, claim, best)
    reasons = [r for r in claim.reason_codes if r not in ("AWAITING_CREDIT", "NO_CREDIT_FOUND")]
    claim.approval_tier = approval_tier(reasons, EvidenceTier(claim.evidence_tier), True)
    if TAMPER.intersection(reasons) and claim.state != ClaimState.FINANCIALLY_CONFIRMED:
        # the money matched, but the document flag stays a reviewer's call (blueprint 8)
        if claim.state != ClaimState.PENDING_REVIEW:
            transition(session, claim, ClaimState.PENDING_REVIEW, actor=actor, reasons=reasons)
        record_decision(
            session,
            claim,
            outcome=ClaimState.PENDING_REVIEW,
            reasons=reasons,
            policy_version=policy.version,
            actor_type="rule",
            actor_id=actor,
            credit_id=best.credit_id,
            snapshot={"candidates": [c.as_dict() for c in candidates[:3]]},
        )
        return claim
    transition(session, claim, ClaimState.FINANCIALLY_CONFIRMED, actor=actor, reasons=reasons)
    record_decision(
        session,
        claim,
        outcome=ClaimState.FINANCIALLY_CONFIRMED,
        reasons=reasons,
        policy_version=policy.version,
        actor_type="rule",
        actor_id=actor,
        credit_id=best.credit_id,
        snapshot={"candidates": [c.as_dict() for c in candidates[:3]]},
    )
    reassess(session, claim, actor)
    return claim


def refresh(session: Session, claim: Claim, actor: str) -> Claim:
    """Recompute reasons, guardrails and the matched credit after a reviewer correction.

    Extraction-time facts (cross-checks, guardrail hits) are replaced; capture, duplicate and
    channel reasons are kept. The claim only moves forward to FINANCIALLY_CONFIRMED when a credit
    now passes every gate; otherwise it stays where it is with updated reasons.
    """
    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, flags = build_facts(session, claim, obligation)
    verdict = evaluate_fields(
        active_rules(session, claim.org_id),
        guardrail_fields(session, claim),
        claim.declared_amount_minor,
        claim.declared_currency,
    )
    kept = [
        r
        for r in claim.reason_codes
        if r not in FACT_FLAGS and not r.startswith("GUARDRAIL_") and r != risk.SUSPECTED_FAKE
    ]
    reasons = list(dict.fromkeys(kept + flags + verdict.review + verdict.needs_info))
    candidates = find_candidates(session, facts, obligation, policy.config)
    best = next((c for c in candidates if c.passed), None)
    can_confirm = ClaimState.FINANCIALLY_CONFIRMED in TRANSITIONS[ClaimState(claim.state)]
    if best is not None and can_confirm and not DUPLICATE.intersection(reasons):
        _attach_credit(session, claim, best)
        claim.approval_tier = approval_tier(reasons, EvidenceTier(claim.evidence_tier), True)
        transition(session, claim, ClaimState.FINANCIALLY_CONFIRMED, actor=actor, reasons=reasons)
    elif reasons != claim.reason_codes:
        before = list(claim.reason_codes)
        claim.reason_codes = reasons
        claim.approval_tier = approval_tier(
            reasons, EvidenceTier(claim.evidence_tier), claim.candidate_credit_id is not None
        )
        audit(
            session,
            actor=actor,
            action="claim.reasons_refreshed",
            entity_type="claim",
            entity_id=claim.claim_id,
            org_id=claim.org_id,
            before={"reason_codes": before},
            after={"reason_codes": reasons},
            correlation_id=claim.correlation_id,
        )
    reassess(session, claim, actor)
    return claim


def rematch_awaiting(session: Session, org_id: uuid.UUID) -> int:
    claims = session.scalars(
        select(Claim)
        .where(
            Claim.org_id == org_id,
            Claim.candidate_credit_id.is_(None),
            Claim.state.in_(
                [ClaimState.AWAITING_FINANCIAL_CONFIRMATION, ClaimState.PENDING_REVIEW]
            ),
        )
        .with_for_update(skip_locked=True)
    ).all()
    claims = [c for c in claims if not DUPLICATE.intersection(c.reason_codes)]
    return sum(1 for c in claims if rematch(session, c).state == ClaimState.FINANCIALLY_CONFIRMED)


# --- resilience -------------------------------------------------------------------------------


def record_failure(session: Session, claim_id: uuid.UUID, error: str) -> Claim | None:
    """Count a failed pipeline attempt; after the limit, hand the claim to a human."""
    claim = session.scalars(
        select(Claim).where(Claim.claim_id == claim_id).with_for_update()
    ).first()
    if claim is None or claim.state != ClaimState.RECEIVED:
        return claim
    obs.PIPELINE_FAILURES.inc()
    claim.pipeline_attempts += 1
    claim.last_pipeline_error = error[:1000]
    if claim.pipeline_attempts >= get_settings().pipeline_max_attempts:
        transition(session, claim, ClaimState.PROCESSING, actor=ACTOR)
        claim.evidence_tier = EvidenceTier.E0
        claim.approval_tier = ApprovalTier.AMBER
        transition(
            session, claim, ClaimState.PENDING_REVIEW, actor=ACTOR, reasons=["PIPELINE_FAILED"]
        )
        log.error(
            "claim %s handed to review after %s failed attempts", claim_id, claim.pipeline_attempts
        )
    return claim


def stuck_claims(session: Session) -> list[Claim]:
    """Claims still RECEIVED well after upload: the task was lost or keeps failing."""
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().stuck_after_seconds)
    return list(
        session.scalars(
            select(Claim)
            .where(Claim.state == ClaimState.RECEIVED, Claim.updated_at < cutoff)
            .order_by(Claim.updated_at)
            .limit(500)
        )
    )
