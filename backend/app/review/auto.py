"""Rules-based approval of clear claims, plus the sampled human audit of it.

The AI never approves: the readers only extract fields. This module is a fixed list of checks
over facts the engine already established. A claim is approved here only when money is confirmed
on our side by an independent source, every hard gate still passes, nothing about it looks
suspicious, the amount is small enough, and it pays the invoice exactly. Anything else is left
for a person, exactly as before. It is off by default (policy `auto_approve_enabled`) and one
switch (`POPS_AUTO_APPROVE_DISABLED`) turns it off everywhere.

Every automatic approval is an ordinary allocation: the ledger trigger re-checks the money
invariants, it is reversible, and it is recorded as a `Decision` by `rule:auto-approve`. A
deterministic sample of them (`auto_approve_audit_pct`) is put in front of a reviewer, and the
overturn rate is the measure that decides whether the cap may rise.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.claims.facts import build_facts
from app.claims.service import record_decision
from app.core.audit import audit
from app.core.config import get_settings
from app.core.security import Principal
from app.fraud import risk
from app.kyc import service as kyc_service
from app.ledger import service as ledger
from app.models import (
    Allocation,
    Claim,
    ClaimState,
    ConsumedReference,
    Credit,
    Decision,
    Obligation,
)
from app.policy.engine import Policy, PolicyConfig, load_policy
from app.reconcile.matching import evaluate
from app.review.service import ReviewError, settle

log = logging.getLogger(__name__)

AUTO_ACTOR = "rule:auto-approve"
VERDICT_ACTOR_TYPE = "audit"
VERDICTS = {"ok": "AUTO_AUDIT_OK", "overturned": "AUTO_AUDIT_OVERTURNED"}


@dataclass
class Inputs:
    """The facts the rules look at, so replay can ask the same questions of past claims."""

    channel: str
    approval_tier: str | None
    evidence_tier: str
    credit_source: str | None
    risk_score: int | None
    open_high_alert: bool
    human_started: bool
    amount_minor: int | None
    currency: str | None
    outstanding_minor: int
    credit_available_minor: int
    # the payer holds a valid KYC verification (app/kyc); only consulted when the policy
    # requires it (`kyc_required_for_auto_approve`)
    kyc_verified: bool = False


def rule_failures(cfg: PolicyConfig, i: Inputs, *, kill_switch: bool = False) -> list[str]:
    """Every rule that stops automatic approval (empty means the claim may be approved)."""
    out: list[str] = []
    if kill_switch:
        out.append("KILL_SWITCH")
    if not cfg.auto_approve_enabled:
        out.append("DISABLED")
    if i.channel not in cfg.auto_approve_channels:
        out.append("CHANNEL_NOT_ENABLED")
    if i.approval_tier != "GREEN":
        out.append("NOT_GREEN")
    # E0..E3 sort in order; a receipt alone (E1) is never enough
    if i.evidence_tier < cfg.auto_approve_min_tier:
        out.append("EVIDENCE_TOO_WEAK")
    if i.credit_source is None:
        out.append("NO_CREDIT")
    elif i.credit_source == "manual":
        out.append("MANUAL_CREDIT")  # attested by one person; a second person must confirm it
    if i.risk_score is None or i.risk_score > cfg.auto_approve_max_risk:
        out.append("RISK_TOO_HIGH")
    if i.open_high_alert:
        out.append("FRAUD_ALERT_OPEN")
    if i.human_started:
        out.append("HUMAN_ALREADY_STARTED")
    if cfg.kyc_required_for_auto_approve and not i.kyc_verified:
        out.append("KYC_NOT_VERIFIED")  # no valid identity check on file: a person decides
    if i.amount_minor is None or not i.currency:
        out.append("AMOUNT_UNKNOWN")
    else:
        cap = cfg.auto_approve_max_minor.get(i.currency)
        if cap is None:
            out.append("NO_CAP_FOR_CURRENCY")
        elif i.amount_minor > cap:
            out.append("OVER_CAP")
        if cfg.needs_second_approver(i.amount_minor, i.currency):
            out.append("HIGH_VALUE")
        # must settle the invoice exactly: partial and over-payments are for a person
        if i.outstanding_minor != i.amount_minor or i.credit_available_minor < i.amount_minor:
            out.append("NOT_EXACT_PAYMENT")
    return out


def _human_started(session: Session, claim: Claim) -> bool:
    return (
        session.scalars(
            select(Decision.decision_id).where(
                Decision.claim_id == claim.claim_id, Decision.outcome == "FIRST_APPROVAL"
            )
        ).first()
        is not None
    )


def maybe_auto_approve(session: Session, claim: Claim) -> bool:
    """Approve the claim if every rule allows it; returns whether it was approved."""
    policy = load_policy(session, claim.org_id)
    if not policy.config.auto_approve_enabled or get_settings().auto_approve_disabled:
        return False
    if claim.state != ClaimState.FINANCIALLY_CONFIRMED or claim.candidate_credit_id is None:
        return False
    obligation = session.get(Obligation, claim.obligation_id)
    credit = session.get(Credit, claim.candidate_credit_id)
    if obligation is None or credit is None:
        return False
    facts, flags = build_facts(session, claim, obligation)
    inputs = Inputs(
        channel=claim.channel,
        approval_tier=claim.approval_tier,
        evidence_tier=claim.evidence_tier,
        credit_source=credit.source,
        risk_score=claim.risk_score,
        open_high_alert=risk.open_high_alert(session, claim.claim_id) is not None,
        human_started=_human_started(session, claim),
        amount_minor=facts.amount_minor,
        currency=facts.currency or obligation.currency,
        outstanding_minor=ledger.balance(session, obligation).outstanding_minor,
        credit_available_minor=ledger.credit_available(session, credit),
        kyc_verified=(
            kyc_service.is_verified(session, claim.org_id, "payer", claim.claimant_ref)
            if policy.config.kyc_required_for_auto_approve
            else False
        ),
    )
    failures = rule_failures(
        policy.config, inputs, kill_switch=get_settings().auto_approve_disabled
    )
    if failures:
        log.info("claim %s left for a person: %s", claim.claim_id, ", ".join(failures))
        return False
    return _approve(session, claim, credit, obligation, policy, facts, flags)


def _approve(
    session: Session,
    claim: Claim,
    credit: Credit,
    obligation: Obligation,
    policy: Policy,
    facts: Any,
    flags: list[str],
) -> bool:
    # the hard gates are re-run now, exactly as for a human confirmation
    cand = evaluate(session, facts, obligation, credit, policy.config)
    if not cand.passed:
        return False
    ref_key = f"ref:{facts.reference}" if facts.reference else None
    if ref_key:
        consumed = session.get(ConsumedReference, ref_key)
        if consumed and consumed.claim_id != claim.claim_id:
            return False
    snapshot = {
        "auto": True,
        "gates": [g.__dict__ for g in cand.gates],
        "score": cand.score,
        "flags": flags,
        "facts": {"amount_minor": facts.amount_minor, "reference": facts.reference},
        "risk_score": claim.risk_score,
        "evidence_tier": claim.evidence_tier,
        "policy": {
            "cap_minor": policy.config.auto_approve_max_minor.get(obligation.currency),
            "max_risk": policy.config.auto_approve_max_risk,
        },
    }
    try:
        # a savepoint, so a ledger refusal leaves the claim exactly as it was, for a person
        with session.begin_nested():
            settle(
                session,
                claim=claim,
                credit=credit,
                obligation=obligation,
                policy=policy,
                flags=flags,
                snapshot=snapshot,
                authorised=facts.amount_minor or 0,
                ref_key=ref_key,
                actor=AUTO_ACTOR,
                actor_type="rule",
                note="approved by rule: independent confirmation, gates passed, within cap",
                extra_reasons=["AUTO_APPROVED"],
            )
    except (ledger.LedgerError, DBAPIError) as exc:
        log.warning("auto-approval of claim %s refused by the ledger: %s", claim.claim_id, exc)
        return False
    return True


# --- sampled human audit ------------------------------------------------------------------------


def is_sampled(claim_id: uuid.UUID, pct: int) -> bool:
    """Deterministic: the same claim is always in or out of the sample."""
    digest = hashlib.sha256(str(claim_id).encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < pct


def list_auto_approved(session: Session, org_id: uuid.UUID, limit: int = 200) -> dict[str, Any]:
    cfg = load_policy(session, org_id).config
    approvals = session.scalars(
        select(Decision)
        .where(
            Decision.org_id == org_id,
            Decision.actor_id == AUTO_ACTOR,
            Decision.outcome == ClaimState.APPROVED,
        )
        .order_by(Decision.created_at.desc())
        .limit(1000)
    ).all()
    ids = [d.claim_id for d in approvals]
    verdicts: dict[uuid.UUID, Decision] = {}
    if ids:
        for v in session.scalars(
            select(Decision)
            .where(Decision.claim_id.in_(ids), Decision.actor_type == VERDICT_ACTOR_TYPE)
            .order_by(Decision.created_at)
        ):
            verdicts[v.claim_id] = v
    amounts: dict[uuid.UUID, tuple[int, str]] = {}
    if ids:
        for a in session.scalars(
            select(Allocation).where(Allocation.claim_id.in_(ids), Allocation.kind == "allocation")
        ):
            if a.claim_id is not None:
                amounts[a.claim_id] = (a.amount_minor, a.currency)
    claims = {c.claim_id: c for c in session.scalars(select(Claim).where(Claim.claim_id.in_(ids)))}
    rows = []
    sampled_n = audited = overturned = 0
    for d in approvals:
        sampled = is_sampled(d.claim_id, cfg.auto_approve_audit_pct)
        verdict = verdicts.get(d.claim_id)
        sampled_n += int(sampled)
        audited += int(verdict is not None)
        overturned += int(verdict is not None and verdict.outcome == VERDICTS["overturned"])
        amount, currency = amounts.get(d.claim_id, (None, None))
        rows.append(
            {
                "claim_id": str(d.claim_id),
                "approved_at": d.created_at.isoformat(),
                "amount_minor": amount,
                "currency": currency,
                "channel": claims[d.claim_id].channel if d.claim_id in claims else None,
                "claim_state": claims[d.claim_id].state if d.claim_id in claims else None,
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
        "enabled": cfg.auto_approve_enabled and not get_settings().auto_approve_disabled,
        "audit_pct": cfg.auto_approve_audit_pct,
        "summary": {
            "approved": len(approvals),
            "sampled": sampled_n,
            "audited": audited,
            "overturned": overturned,
            "overturn_rate": round(overturned / audited, 4) if audited else None,
        },
        "claims": rows[:limit],
    }


def record_verdict(
    session: Session, p: Principal, claim_id: uuid.UUID, verdict: str, note: str | None
) -> Decision:
    """A reviewer's judgement on an automatic approval. Overturning does not move money: reverse
    the allocation separately (approver role), so every correction has its own audit trail."""
    if verdict not in VERDICTS:
        raise ReviewError("BAD_VERDICT", f"verdict must be one of {sorted(VERDICTS)}", 422)
    claim = session.scalars(
        select(Claim).where(Claim.claim_id == claim_id, Claim.org_id == p.org_id).with_for_update()
    ).first()
    if claim is None:
        raise ReviewError("NOT_FOUND", "claim not found", 404)
    auto = session.scalars(
        select(Decision.decision_id).where(
            Decision.claim_id == claim_id,
            Decision.actor_id == AUTO_ACTOR,
            Decision.outcome == ClaimState.APPROVED,
        )
    ).first()
    if auto is None:
        raise ReviewError("NOT_AUTO_APPROVED", "this claim was not approved automatically", 409)
    if session.scalars(
        select(Decision.decision_id).where(
            Decision.claim_id == claim_id, Decision.actor_type == VERDICT_ACTOR_TYPE
        )
    ).first():
        raise ReviewError("ALREADY_AUDITED", "this approval already has a verdict", 409)
    policy = load_policy(session, claim.org_id)
    decision = record_decision(
        session,
        claim,
        outcome=VERDICTS[verdict],
        reasons=[],
        policy_version=policy.version,
        actor_type=VERDICT_ACTOR_TYPE,
        actor_id=p.actor,
        snapshot={"audited_at": datetime.now(UTC).isoformat()},
        note=note,
    )
    audit(
        session,
        actor=p.actor,
        action=f"auto_approval.audit_{verdict}",
        entity_type="claim",
        entity_id=claim_id,
        org_id=p.org_id,
        after={"note": note},
        correlation_id=claim.correlation_id,
    )
    return decision
