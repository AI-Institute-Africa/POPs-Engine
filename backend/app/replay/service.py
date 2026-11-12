"""Replay and accuracy reports: measure the engine on real, already-decided claims.

Nothing here writes. `what_if` re-runs reconciliation and fraud scoring for past claims under the
current or a draft policy and lists what would change. `accuracy` uses outcomes as ground truth:
a confirmed allocation tells us what the receipt really said (the bank record matched it), and an
admin's fake / not-fake decision tells us whether a fraud signal was right.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.facts import build_facts, guardrail_fields
from app.core.config import get_settings
from app.fraud import risk
from app.models import (
    Allocation,
    Claim,
    ClaimState,
    Credit,
    Decision,
    ExtractedField,
    FraudAlert,
    Obligation,
    VerificationRun,
)
from app.policy.engine import (
    Policy,
    PolicyConfig,
    approval_tier,
    evidence_tier_for_source,
    load_policy,
)
from app.reconcile.matching import find_candidates

LEVELS = {"low": 0, "medium": 1, "high": 2, None: 0}


def what_if(
    session: Session,
    org_id: uuid.UUID,
    draft: dict[str, Any] | None = None,
    since_days: int = 90,
    limit: int = 1000,
) -> dict[str, Any]:
    policy = (
        Policy(version="draft", config=PolicyConfig.model_validate(draft))
        if draft is not None
        else load_policy(session, org_id)
    )
    since = datetime.now(UTC) - timedelta(days=since_days)
    claims = session.scalars(
        select(Claim)
        .where(
            Claim.org_id == org_id,
            Claim.created_at >= since,
            Claim.state.not_in([ClaimState.RECEIVED, ClaimState.PROCESSING]),
        )
        .order_by(Claim.created_at.desc())
        .limit(limit)
    ).all()
    changes: dict[str, int] = defaultdict(int)
    rows = []
    levels_now: dict[str, int] = defaultdict(int)
    levels_replay: dict[str, int] = defaultdict(int)
    auto_matched = auto_ok = 0
    auto_blockers: dict[str, int] = defaultdict(int)
    try:
        for c in claims:
            ob = session.get(Obligation, c.obligation_id)
            assert ob is not None
            facts, flags = build_facts(session, c, ob)
            cands = find_candidates(session, facts, ob, policy.config)
            best = next((x for x in cands if x.passed), None)
            a = risk.assess(session, c, ob, policy, facts, cands, guardrail_fields(session, c))
            levels_now[c.risk_level or "low"] += 1
            levels_replay[a.level] += 1
            if best is not None:
                failures = _auto_approve_blockers(session, policy, c, ob, facts, best, a)
                auto_matched += 1
                auto_ok += int(not failures)
                for f in failures:
                    auto_blockers[f] += 1
            kinds = []
            matched_now = c.candidate_credit_id is not None
            if best and not matched_now and c.state not in (ClaimState.REJECTED,):
                kinds.append("newly_matched")
            if (
                matched_now
                and best is None
                and c.state
                not in (
                    ClaimState.APPROVED,
                    ClaimState.PARTIALLY_ALLOCATED,
                    ClaimState.REVERSED_OR_REFUNDED,
                )
            ):
                kinds.append("match_lost")
            if LEVELS[a.level] > LEVELS[c.risk_level]:
                kinds.append("newly_suspected_fake" if a.level == "high" else "risk_up")
            elif LEVELS[a.level] < LEVELS[c.risk_level]:
                kinds.append("risk_down")
            for k in kinds:
                changes[k] += 1
            if kinds:
                rows.append(
                    {
                        "claim_id": str(c.claim_id),
                        "state": c.state,
                        "changes": kinds,
                        "risk_now": c.risk_level,
                        "risk_replay": a.level,
                        "score_now": c.risk_score,
                        "score_replay": a.score,
                        "signals_replay": [s.code for s in a.signals],
                        "matched_now": matched_now,
                        "matched_replay": str(best.credit_id) if best else None,
                    }
                )
    finally:
        session.rollback()  # belt and braces: a replay must never persist anything
    return {
        "policy_version": policy.version,
        "claims_replayed": len(claims),
        "changes": dict(changes),
        "risk_levels": {"now": dict(levels_now), "replay": dict(levels_replay)},
        # an estimate that assumes each invoice and credit were unused when the claim
        # arrived; blockers say which rule held the rest back
        "auto_approve": {
            "claims_with_a_match": auto_matched,
            "would_auto_approve": auto_ok,
            "blockers": dict(sorted(auto_blockers.items(), key=lambda kv: -kv[1])),
        },
        "changed_claims": rows,
    }


def _auto_approve_blockers(
    session: Session, policy: Policy, claim: Claim, ob: Obligation, facts: Any, best: Any, a: Any
) -> list[str]:
    """Which auto-approval rules would hold this past claim back under `policy`."""
    from app.kyc import service as kyc_service
    from app.review.auto import Inputs, rule_failures

    credit = session.get(Credit, best.credit_id)
    assert credit is not None
    resolved = {
        "AWAITING_CREDIT",
        "NO_CREDIT_FOUND",
        "SUSPECTED_FAKE",
        "AUTO_APPROVED",
        "PARTIAL_PAYMENT",
        "OVERPAYMENT",
    }
    reasons = [r for r in claim.reason_codes if r not in resolved]
    if a.level == "high":
        reasons.append("SUSPECTED_FAKE")
    tier = evidence_tier_for_source(credit.source)
    return rule_failures(
        policy.config,
        Inputs(
            channel=claim.channel,
            approval_tier=str(approval_tier(reasons, tier, True)),
            evidence_tier=str(tier),
            credit_source=credit.source,
            risk_score=a.score,
            open_high_alert=a.level == "high",
            human_started=False,
            amount_minor=facts.amount_minor,
            currency=facts.currency or ob.currency,
            outstanding_minor=ob.amount_minor,
            credit_available_minor=credit.amount_minor,
            kyc_verified=(
                kyc_service.is_verified(session, claim.org_id, "payer", claim.claimant_ref)
                if policy.config.kyc_required_for_auto_approve
                else False
            ),
        ),
        kill_switch=get_settings().auto_approve_disabled,
    )


def accuracy(session: Session, org_id: uuid.UUID, since_days: int = 90) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=since_days)
    # --- readers: confirmed allocations are ground truth for amount and reference -------------
    truth = session.execute(
        select(Claim, Credit)
        .join(
            Allocation, (Allocation.claim_id == Claim.claim_id) & (Allocation.kind == "allocation")
        )
        .join(Credit, Credit.credit_id == Allocation.credit_id)
        .where(Claim.org_id == org_id, Claim.created_at >= since)
    ).all()
    per_reader: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    per_group: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"claims": 0, "amount_ok": 0, "reference_ok": 0, "auto_agreed": 0, "corrected": 0}
    )
    seen: set[uuid.UUID] = set()
    for claim, credit in truth:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        run = session.scalars(
            select(VerificationRun)
            .where(VerificationRun.claim_id == claim.claim_id)
            .order_by(VerificationRun.created_at.desc())
            .limit(1)
        ).first()
        readings = (
            next(
                (
                    c.get("readings")
                    for c in (run.checks if run else [])
                    if c.get("check") == "extraction"
                ),
                None,
            )
            or {}
        )
        fields = {
            f.name: f
            for f in session.scalars(
                select(ExtractedField).where(
                    ExtractedField.claim_id == claim.claim_id,
                    ExtractedField.run_id == (run.run_id if run else None),
                    ExtractedField.corrected_by.is_(None),
                )
            )
        }
        corrected = (
            session.scalars(
                select(ExtractedField.field_id).where(
                    ExtractedField.claim_id == claim.claim_id,
                    ExtractedField.corrected_by.is_not(None),
                )
            ).first()
            is not None
        )
        want = {"amount": str(credit.amount_minor), "transaction_id": credit.reference_norm}
        provider = fields["provider"].normalised_value if "provider" in fields else None
        g = per_group[f"{claim.channel} / {provider or 'unknown provider'}"]
        g["claims"] += 1
        g["corrected"] += int(corrected)
        amount_f, ref_f = fields.get("amount"), fields.get("transaction_id")
        g["amount_ok"] += int(bool(amount_f and amount_f.normalised_value == want["amount"]))
        g["reference_ok"] += int(bool(ref_f and ref_f.normalised_value == want["transaction_id"]))
        g["auto_agreed"] += int(bool(amount_f and amount_f.agreed and ref_f and ref_f.agreed))
        for name, by_reader in readings.items():
            if name not in want:
                continue
            for reader, value in (by_reader or {}).items():
                stat = per_reader[reader][name]
                stat[1] += 1
                stat[0] += int(value == want[name])

    def pct(a: int, b: int) -> float | None:
        return round(100 * a / b, 1) if b else None

    readers = {
        reader: {
            name: {"correct": ok, "read": n, "accuracy_pct": pct(ok, n)}
            for name, (ok, n) in fields.items()
        }
        for reader, fields in per_reader.items()
    }
    groups = {
        k: {
            **v,
            "amount_accuracy_pct": pct(v["amount_ok"], v["claims"]),
            "reference_accuracy_pct": pct(v["reference_ok"], v["claims"]),
            "auto_agreed_pct": pct(v["auto_agreed"], v["claims"]),
            "corrected_pct": pct(v["corrected"], v["claims"]),
        }
        for k, v in per_group.items()
    }

    # --- fraud: admin decisions are ground truth for signals ----------------------------------
    resolved = session.scalars(
        select(FraudAlert).where(
            FraudAlert.org_id == org_id,
            FraudAlert.created_at >= since,
            FraudAlert.status.in_(["confirmed_fake", "false_positive"]),
        )
    ).all()
    per_signal: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for a in resolved:
        for s in a.signals:
            per_signal[s["code"]][0 if a.status == "confirmed_fake" else 1] += 1
    signals = {
        code: {"confirmed_fake": f, "cleared": c, "precision_pct": pct(f, f + c)}
        for code, (f, c) in sorted(per_signal.items(), key=lambda kv: -sum(kv[1]))
    }
    fakes = [a for a in resolved if a.status == "confirmed_fake"]
    # fakes the score missed: rejected as tampered by a reviewer without any alert
    alerted = {
        a.claim_id for a in session.scalars(select(FraudAlert).where(FraudAlert.org_id == org_id))
    }
    missed = session.scalars(
        select(Decision.claim_id).where(
            Decision.org_id == org_id,
            Decision.created_at >= since,
            Decision.outcome == ClaimState.REJECTED,
            Decision.reason_codes.contains(["TAMPERED_DOCUMENT"]),
        )
    ).all()
    missed_ids = {m for m in missed if m not in alerted}
    return {
        "since_days": since_days,
        "readers": {"confirmed_claims": len(seen), "by_reader": readers, "by_channel": groups},
        "fraud": {
            "resolved_alerts": len(resolved),
            "confirmed_fakes": len(fakes),
            "overall_precision_pct": pct(len(fakes), len(resolved)),
            "by_signal": signals,
            "fakes_missed_by_score": len(missed_ids),
        },
    }
