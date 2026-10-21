"""Claim verification pipeline (blueprint 4.1). Review-only: it never allocates money.

Each run is recorded with pipeline, policy and extractor versions so the outcome can be replayed.
Steps that fail or are unsure route the claim to a human (fail closed, blueprint 11.2).
"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.claims.facts import build_facts
from app.claims.service import record_decision, transition
from app.claims.states import REVIEWABLE
from app.extraction import preprocess
from app.extraction.readers.base import Reader
from app.extraction.service import extract
from app.intake.files import phash_distance, prepare
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
from app.reconcile.matching import find_candidates

PIPELINE_VERSION = "pipeline-1.0"
ACTOR = "rule:pipeline"

NEEDS_INFO = {"CAPTURE_TOO_SMALL", "CAPTURE_BLURRY", "UNREADABLE", "MISSING_AMOUNT"}
DUPLICATE = {
    "DUPLICATE_FILE",
    "DUPLICATE_NEAR_FILE",
    "DUPLICATE_REFERENCE",
    "REFERENCE_ALREADY_CONSUMED",
}
TERMINAL_OR_DONE = {ClaimState.REJECTED, ClaimState.REVERSED_OR_REFUNDED}


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
    if ev.phash:
        # MVP scan of recent evidence; move to a BK-tree or pg extension when volume demands it
        recent = session.execute(
            select(Evidence.claim_id, Evidence.phash)
            .where(Evidence.claim_id != claim.claim_id, Evidence.phash.is_not(None))
            .order_by(Evidence.received_at.desc())
            .limit(5000)
        ).all()
        near = {
            cid
            for cid, ph in recent
            if cid not in others
            and ph
            and phash_distance(ev.phash, ph) <= policy.config.phash_duplicate_distance
        }
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


def process_claim(
    session: Session, claim_id: uuid.UUID, readers: list[Reader] | None = None
) -> Claim:
    """Run the pipeline for a RECEIVED claim. The caller commits."""
    claim = session.scalars(select(Claim).where(Claim.claim_id == claim_id).with_for_update()).one()
    if claim.state != ClaimState.RECEIVED:
        return claim  # idempotent: a retried task does nothing
    transition(session, claim, ClaimState.PROCESSING, actor=ACTOR)
    policy = load_policy(session, claim.org_id)
    cfg = policy.config
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    ev = session.scalars(
        select(Evidence)
        .where(Evidence.claim_id == claim.claim_id)
        .order_by(Evidence.received_at.desc())
    ).first()

    reasons: list[str] = []
    checks: list[dict] = []
    dup_others: set[uuid.UUID] = set()
    run = VerificationRun(
        claim_id=claim.claim_id,
        pipeline_version=PIPELINE_VERSION,
        policy_version=policy.version,
        outcome="RUNNING",
    )
    session.add(run)
    session.flush()

    if ev is None:
        reasons.append("UNREADABLE")
    else:
        file_reasons, others = _file_duplicates(session, claim, ev, policy)
        reasons += file_reasons
        dup_others |= others
        checks.append({"layer": 1, "check": "duplicate_file", "passed": not file_reasons})

        raw = get_storage().get(ev.object_key)
        image = prepare(raw, max_bytes=len(raw) + 1).image
        quality = preprocess.assess(image)
        problems = quality.problems(cfg.min_image_side_px, cfg.min_sharpness)
        reasons += problems
        checks.append(
            {
                "layer": 1,
                "check": "capture_quality",
                "passed": not problems,
                "sharpness": round(quality.sharpness, 1),
            }
        )

        result = extract(image, raw, cfg.critical_fields, cfg.single_reader_min_confidence, readers)
        run.model_versions = result.versions
        if result.failures:
            reasons.append("READER_UNAVAILABLE")
        checks.append(
            {
                "layer": 2,
                "check": "extraction",
                "readers": list(result.versions),
                "failures": result.failures,
            }
        )
        for f in result.fields.values():
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
    elif NEEDS_INFO.intersection(reasons):
        target = ClaimState.NEEDS_INFORMATION
    elif best is not None:
        credit = session.get(Credit, best.credit_id)
        assert credit is not None
        claim.candidate_credit_id = credit.credit_id
        claim.evidence_tier = evidence_tier_for_source(credit.source)
        transition(session, claim, ClaimState.DOCUMENT_VERIFIED, actor=ACTOR)
        target = ClaimState.FINANCIALLY_CONFIRMED
    elif not reasons or reasons == ["MISSING_REFERENCE"]:
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
    run.checks = checks
    run.flags = reasons
    run.outcome = target
    record_decision(
        session,
        claim,
        outcome=target,
        reasons=reasons,
        policy_version=policy.version,
        actor_type="rule",
        actor_id=ACTOR,
        credit_id=claim.candidate_credit_id,
        snapshot={
            "run_id": str(run.run_id),
            "evidence_sha256": ev.sha256 if ev else None,
            "facts": {
                "amount_minor": facts.amount_minor,
                "currency": facts.currency,
                "reference": facts.reference,
                "occurred_at": facts.occurred_at.isoformat() if facts.occurred_at else None,
            },
        },
    )
    return claim


def rematch(session: Session, claim: Claim, actor: str = ACTOR) -> Claim:
    """Re-run reconciliation for a claim waiting on money (credit-first matching, blueprint 5.2)."""
    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, flags = build_facts(session, claim, obligation)
    candidates = find_candidates(session, facts, obligation, policy.config)
    best = next((c for c in candidates if c.passed), None)
    if best is None:
        return claim
    credit = session.get(Credit, best.credit_id)
    assert credit is not None
    claim.candidate_credit_id = credit.credit_id
    claim.evidence_tier = evidence_tier_for_source(credit.source)
    reasons = [r for r in claim.reason_codes if r != "AWAITING_CREDIT"]
    claim.approval_tier = approval_tier(reasons, EvidenceTier(claim.evidence_tier), True)
    transition(session, claim, ClaimState.FINANCIALLY_CONFIRMED, actor=actor, reasons=reasons)
    record_decision(
        session,
        claim,
        outcome=ClaimState.FINANCIALLY_CONFIRMED,
        reasons=reasons,
        policy_version=policy.version,
        actor_type="rule",
        actor_id=actor,
        credit_id=credit.credit_id,
        snapshot={"candidates": [c.as_dict() for c in candidates[:3]]},
    )
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
