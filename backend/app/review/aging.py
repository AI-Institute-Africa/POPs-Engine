"""Nudge admins about claims that have waited too long for a human decision."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.service import REVIEW_WAITING
from app.entitlements.outbox import enqueue_event
from app.models import Claim
from app.policy.engine import load_policy


def review_aging(session: Session, now: datetime | None = None) -> int:
    """Send one `ops.review_overdue` per claim per SLA level reached; returns nudges sent."""
    now = now or datetime.now(UTC)
    claims = session.scalars(
        select(Claim)
        .where(
            Claim.state.in_([s.value for s in REVIEW_WAITING]), Claim.review_entered_at.is_not(None)
        )
        .order_by(Claim.review_entered_at)
        .limit(500)
        .with_for_update(skip_locked=True)
    ).all()
    sla_by_org: dict[uuid.UUID, list[int]] = {}
    sent = 0
    for claim in claims:
        sla = sla_by_org.setdefault(
            claim.org_id, load_policy(session, claim.org_id).config.review_sla_minutes
        )
        waited = (now - claim.review_entered_at).total_seconds() / 60  # type: ignore[operator]
        level = sum(1 for minutes in sla if waited >= minutes)
        if level <= claim.review_escalation:
            continue
        claim.review_escalation = level
        enqueue_event(
            session,
            claim.org_id,
            "ops.review_overdue",
            f"review_aging:{claim.claim_id}:{level}",
            {
                "text": f"A proof of payment has been waiting for review for {int(waited)} minutes "
                f"(reminder {level} of {len(sla)}). Claim {claim.claim_id}.",
                "claim_id": str(claim.claim_id),
                "state": claim.state,
                "waiting_minutes": int(waited),
                "level": level,
            },
        )
        sent += 1
    return sent
