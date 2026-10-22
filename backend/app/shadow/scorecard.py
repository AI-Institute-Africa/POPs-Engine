"""Shadow-mode scorecard (blueprint innovation 8).

Compares what the proposers suggested with what a human reviewer later decided. The three
numbers finance asks before automating anything:

- agreement rate: proposals that matched the human outcome bucket;
- false-approval rate: proposals that said "approve" while the reviewer rejected the claim;
- false-hold rate: proposals that said "reject" or "needs_info" while the reviewer approved.

Only reviewer decisions count as ground truth; rule decisions and unscored claims are
excluded. Everything here is read-only over append-only tables.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Decision, ShadowProposal

# human outcome buckets; anything else (e.g. duplicates still on hold) is not scored
APPROVE_HUMAN = {"APPROVED", "PARTIALLY_ALLOCATED"}
REJECT_HUMAN = {"REJECTED"}
NEEDS_INFO_HUMAN = {"NEEDS_INFORMATION"}


def _human_bucket(outcome: str) -> str | None:
    if outcome in APPROVE_HUMAN:
        return "approve"
    if outcome in REJECT_HUMAN:
        return "reject"
    if outcome in NEEDS_INFO_HUMAN:
        return "needs_info"
    return None


@dataclass
class ProposerScore:
    proposer: str
    proposals: int = 0
    scored: int = 0  # proposals with a later reviewer decision to compare against
    agreements: int = 0
    false_approvals: int = 0  # proposed approve, human rejected
    false_holds: int = 0  # proposed reject/needs_info, human approved
    agreement_rate: float = 0.0
    false_approval_rate: float = 0.0
    false_hold_rate: float = 0.0
    by_outcome: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "proposer": self.proposer,
            "proposals": self.proposals,
            "scored": self.scored,
            "agreements": self.agreements,
            "false_approvals": self.false_approvals,
            "false_holds": self.false_holds,
            "agreement_rate": round(self.agreement_rate, 4),
            "false_approval_rate": round(self.false_approval_rate, 4),
            "false_hold_rate": round(self.false_hold_rate, 4),
            "by_outcome": self.by_outcome,
        }


@dataclass
class Scorecard:
    window_days: int
    proposals: int = 0
    scored: int = 0
    proposers: list[ProposerScore] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "proposals": self.proposals,
            "scored": self.scored,
            "proposers": [p.as_dict() for p in self.proposers],
        }


def scorecard(session: Session, org_id: uuid.UUID, days: int = 30) -> Scorecard:
    """Score every proposal made for this organisation's claims in the window."""
    since = datetime.now(UTC) - timedelta(days=days)
    proposals = list(
        session.scalars(
            select(ShadowProposal).where(
                ShadowProposal.org_id == org_id, ShadowProposal.created_at >= since
            )
        )
    )
    card = Scorecard(window_days=days, proposals=len(proposals))
    if not proposals:
        return card

    claim_ids = {p.claim_id for p in proposals}
    claims = {
        c.claim_id: c
        for c in session.scalars(select(Claim).where(Claim.claim_id.in_(claim_ids))).all()
    }
    # latest reviewer decision per claim
    decisions: dict[uuid.UUID, Decision] = {}
    rows = session.scalars(
        select(Decision)
        .where(Decision.claim_id.in_(claim_ids), Decision.actor_type == "reviewer")
        .order_by(Decision.created_at)
    ).all()
    for d in rows:
        decisions[d.claim_id] = d

    scores: dict[str, ProposerScore] = {}
    for p in proposals:
        s = scores.setdefault(p.proposer, ProposerScore(proposer=p.proposer))
        s.proposals += 1
        s.by_outcome[p.proposed_outcome] = s.by_outcome.get(p.proposed_outcome, 0) + 1
        # ground truth: the reviewer's decision, only if it came after the proposal
        decision = decisions.get(p.claim_id)
        if decision is None or decision.created_at < p.created_at:
            continue
        human = _human_bucket(decision.outcome)
        if human is None or claims.get(p.claim_id) is None:
            continue
        s.scored += 1
        if p.proposed_outcome == "approve" and human == "reject":
            s.false_approvals += 1
        elif p.proposed_outcome in ("reject", "needs_info") and human == "approve":
            s.false_holds += 1
        elif p.proposed_outcome == human:
            s.agreements += 1

    for s in scores.values():
        if s.scored:
            s.agreement_rate = s.agreements / s.scored
            proposed_approvals = s.by_outcome.get("approve", 0)
            proposed_holds = s.by_outcome.get("reject", 0) + s.by_outcome.get("needs_info", 0)
            # rates over what was proposed in that bucket (scored subset approximates it;
            # denominators never divide by zero)
            s.false_approval_rate = s.false_approvals / max(1, proposed_approvals)
            s.false_hold_rate = s.false_holds / max(1, proposed_holds)
    card.scored = sum(s.scored for s in scores.values())
    card.proposers = sorted(scores.values(), key=lambda s: s.proposals, reverse=True)
    return card
