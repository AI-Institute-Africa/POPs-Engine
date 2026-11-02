"""Provisional access: time-boxed, capped access while a receipt waits for the money to show up.

The engine only reports it. Each host system decides what "access" means, through two surfaces:
signed events (`entitlement.provisional`, `entitlement.revoked`) and `GET /obligations/{id}/access`.

A grant needs every one of these (anything missing means no grant, never a guess):
  policy switch on, claim waiting for money with nothing else wrong, risk assessed and low, no
  earlier fake from the same payer, amount under the per-claim cap, and room under the payer's
  and the organisation's daily caps for that currency.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.audit import audit
from app.entitlements.outbox import enqueue_event, entitlement_payload
from app.ledger import service as ledger
from app.models import Claim, ClaimState, Obligation, ProvisionalGrant
from app.policy.engine import PolicyConfig, load_policy

ACTIVE = "active"
ACTOR = "rule:provisional"
EXPIRING_SOON = timedelta(hours=6)

# claim state -> how an active grant ends
_CONFIRMED = {ClaimState.APPROVED, ClaimState.PARTIALLY_ALLOCATED}
_REVOKED = {
    ClaimState.REJECTED: "rejected",
    ClaimState.REVERSED_OR_REFUNDED: "reversed",
    ClaimState.NEEDS_INFORMATION: "needs_information",
}


def _active_for_claim(session: Session, claim_id: uuid.UUID) -> ProvisionalGrant | None:
    return session.scalars(
        select(ProvisionalGrant).where(
            ProvisionalGrant.claim_id == claim_id, ProvisionalGrant.status == ACTIVE
        )
    ).first()


def _sum(session: Session, *where: Any) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(ProvisionalGrant.exposure_minor), 0)).where(*where)
        )
        or 0
    )


def _day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def ineligible_reason(
    session: Session, claim: Claim, obligation: Obligation, cfg: PolicyConfig, now: datetime
) -> tuple[str | None, int]:
    """Why this claim cannot get provisional access (None if it can), and the exposure amount."""
    from app.claims.facts import build_facts

    if not cfg.provisional_enabled:
        return "DISABLED", 0
    if claim.state != ClaimState.AWAITING_FINANCIAL_CONFIRMATION:
        return "NOT_WAITING_FOR_MONEY", 0
    # nothing but "waiting for a credit": no duplicate, tamper, missing detail, channel or
    # guardrail concern
    if list(claim.reason_codes) != ["AWAITING_CREDIT"]:
        return "OTHER_REASONS", 0
    if claim.risk_score is None or claim.risk_score >= cfg.fraud_medium_score:
        return "RISK_NOT_LOW", 0
    if any(s.get("code") == "PAYER_HISTORY" for s in claim.risk_signals):
        return "PAYER_HISTORY", 0
    facts, _ = build_facts(session, claim, obligation)
    currency = obligation.currency
    if facts.amount_minor is None or (facts.currency and facts.currency != currency):
        return "AMOUNT_UNKNOWN", 0
    outstanding = ledger.balance(session, obligation).outstanding_minor
    exposure = min(facts.amount_minor, outstanding)
    if exposure <= 0:
        return "NOTHING_OUTSTANDING", 0
    per_claim = cfg.provisional_max_minor.get(currency)
    per_learner = cfg.provisional_learner_cap_minor.get(currency)
    per_day = cfg.provisional_org_daily_cap_minor.get(currency)
    if per_claim is None or per_learner is None or per_day is None:
        return "NO_CAP_FOR_CURRENCY", 0
    if exposure > per_claim:
        return "OVER_CLAIM_CAP", 0
    learner_now = _sum(
        session,
        ProvisionalGrant.org_id == claim.org_id,
        ProvisionalGrant.learner_ref == obligation.learner_ref,
        ProvisionalGrant.currency == currency,
        ProvisionalGrant.status == ACTIVE,
    )
    if learner_now + exposure > per_learner:
        return "OVER_LEARNER_CAP", 0
    today = _sum(
        session,
        ProvisionalGrant.org_id == claim.org_id,
        ProvisionalGrant.currency == currency,
        ProvisionalGrant.granted_at >= _day_start(now),
    )
    if today + exposure > per_day:
        return "OVER_DAILY_CAP", 0
    return None, exposure


def grant_if_eligible(
    session: Session, claim: Claim, now: datetime | None = None
) -> ProvisionalGrant | None:
    now = now or datetime.now(UTC)
    existing = _active_for_claim(session, claim.claim_id)
    if existing is not None:
        return existing
    policy = load_policy(session, claim.org_id)
    obligation = session.get(Obligation, claim.obligation_id)
    if obligation is None:
        return None
    reason, exposure = ineligible_reason(session, claim, obligation, policy.config, now)
    if reason is not None:
        return None
    grant = ProvisionalGrant(
        org_id=claim.org_id,
        obligation_id=claim.obligation_id,
        claim_id=claim.claim_id,
        learner_ref=obligation.learner_ref,
        currency=obligation.currency,
        exposure_minor=exposure,
        status=ACTIVE,
        granted_at=now,
        expires_at=now + timedelta(hours=policy.config.provisional_ttl_hours),
    )
    session.add(grant)
    session.flush()
    claim.provisional_until = grant.expires_at
    audit(
        session,
        actor=ACTOR,
        action="provisional.granted",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=claim.org_id,
        after={
            "grant_id": str(grant.grant_id),
            "exposure_minor": exposure,
            "expires_at": grant.expires_at.isoformat(),
            "policy": policy.version,
        },
        correlation_id=claim.correlation_id,
    )
    bal = ledger.balance(session, obligation)
    enqueue_event(
        session,
        claim.org_id,
        "entitlement.provisional",
        f"provisional:{grant.grant_id}:granted",
        {
            **entitlement_payload(obligation, bal, "provisional_granted"),
            "provisional": True,
            "grant_id": str(grant.grant_id),
            "claim_id": str(claim.claim_id),
            "exposure_minor": exposure,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    obs.PROVISIONAL_EVENTS.labels("granted").inc()
    return grant


def _close(
    session: Session,
    grant: ProvisionalGrant,
    claim: Claim | None,
    status: str,
    reason: str,
    now: datetime,
) -> None:
    grant.status = status
    grant.resolved_at = now
    grant.resolved_reason = reason
    if claim is not None:
        claim.provisional_until = None
    audit(
        session,
        actor=ACTOR,
        action=f"provisional.{status}",
        entity_type="claim",
        entity_id=grant.claim_id,
        org_id=grant.org_id,
        after={"grant_id": str(grant.grant_id), "reason": reason},
    )
    obs.PROVISIONAL_EVENTS.labels(status).inc()
    if status in ("revoked", "expired"):
        obligation = session.get(Obligation, grant.obligation_id)
        if obligation is not None:
            bal = ledger.balance(session, obligation)
            enqueue_event(
                session,
                grant.org_id,
                "entitlement.revoked",
                f"provisional:{grant.grant_id}:{status}",
                {
                    **entitlement_payload(obligation, bal, f"provisional_{reason}"),
                    "provisional": True,
                    "grant_id": str(grant.grant_id),
                    "claim_id": str(grant.claim_id),
                    "revoked_cause": reason,
                },
            )


def resolve_for_state(session: Session, claim: Claim, target: ClaimState) -> None:
    """Called when a claim changes state: confirm or revoke its active grant, if any."""
    if target not in _CONFIRMED and target not in _REVOKED:
        return
    grant = _active_for_claim(session, claim.claim_id)
    if grant is None:
        return
    now = datetime.now(UTC)
    if target in _CONFIRMED:
        _close(session, grant, claim, "confirmed", "approved", now)
    else:
        _close(session, grant, claim, "revoked", _REVOKED[target], now)


def revoke_for_claim(session: Session, claim: Claim, reason: str) -> bool:
    grant = _active_for_claim(session, claim.claim_id)
    if grant is None:
        return False
    _close(session, grant, claim, "revoked", reason, datetime.now(UTC))
    return True


def expire_due(session: Session, now: datetime | None = None) -> int:
    """End grants whose window passed without a matching credit; tell the host and admins."""
    now = now or datetime.now(UTC)
    due = session.scalars(
        select(ProvisionalGrant)
        .where(ProvisionalGrant.status == ACTIVE, ProvisionalGrant.expires_at <= now)
        .order_by(ProvisionalGrant.expires_at)
        .limit(500)
        .with_for_update(skip_locked=True)
    ).all()
    for grant in due:
        claim = session.get(Claim, grant.claim_id)
        _close(session, grant, claim, "expired", "expired", now)
        enqueue_event(
            session,
            grant.org_id,
            "ops.provisional_expired",
            f"provisional:{grant.grant_id}:expired:ops",
            {
                "text": "Provisional access expired with no matching payment found "
                f"(claim {grant.claim_id}).",
                "claim_id": str(grant.claim_id),
                "grant_id": str(grant.grant_id),
                "exposure_minor": grant.exposure_minor,
                "currency": grant.currency,
            },
        )
    return len(due)


def active_exposure(session: Session, org_id: uuid.UUID | None = None) -> dict[str, int]:
    q = select(ProvisionalGrant.currency, func.sum(ProvisionalGrant.exposure_minor)).where(
        ProvisionalGrant.status == ACTIVE
    )
    if org_id is not None:
        q = q.where(ProvisionalGrant.org_id == org_id)
    return {
        cur: int(total or 0)
        for cur, total in session.execute(q.group_by(ProvisionalGrant.currency))
    }


def refresh_gauges(session: Session) -> None:
    totals = active_exposure(session)
    for currency in ("USD", "ZWG", *totals):
        obs.PROVISIONAL_EXPOSURE.labels(currency).set(totals.get(currency, 0))


def exposure_report(session: Session, org_id: uuid.UUID, now: datetime | None = None) -> dict:
    """What finance asks daily: value at risk, what happened to recent grants, what ends soon."""
    now = now or datetime.now(UTC)
    cfg = load_policy(session, org_id).config
    since = now - timedelta(days=7)
    counts = {
        status: int(n)
        for status, n in session.execute(
            select(ProvisionalGrant.status, func.count())
            .where(ProvisionalGrant.org_id == org_id, ProvisionalGrant.granted_at >= since)
            .group_by(ProvisionalGrant.status)
        )
    }
    granted = sum(counts.values())
    settled = counts.get("confirmed", 0) + counts.get("revoked", 0) + counts.get("expired", 0)
    soon = session.scalars(
        select(ProvisionalGrant)
        .where(
            ProvisionalGrant.org_id == org_id,
            ProvisionalGrant.status == ACTIVE,
            ProvisionalGrant.expires_at <= now + EXPIRING_SOON,
        )
        .order_by(ProvisionalGrant.expires_at)
        .limit(50)
    ).all()
    return {
        "enabled": cfg.provisional_enabled,
        "active_exposure_minor": active_exposure(session, org_id),
        "caps": {
            "per_claim_minor": cfg.provisional_max_minor,
            "per_learner_minor": cfg.provisional_learner_cap_minor,
            "org_daily_minor": cfg.provisional_org_daily_cap_minor,
            "ttl_hours": cfg.provisional_ttl_hours,
        },
        "last_7_days": {
            "granted": granted,
            "active": counts.get("active", 0),
            "confirmed": counts.get("confirmed", 0),
            "revoked": counts.get("revoked", 0),
            "expired": counts.get("expired", 0),
            "confirmed_pct": round(100 * counts.get("confirmed", 0) / settled, 1)
            if settled
            else None,
        },
        "expiring_soon": [
            {
                "grant_id": str(g.grant_id),
                "claim_id": str(g.claim_id),
                "obligation_id": str(g.obligation_id),
                "exposure_minor": g.exposure_minor,
                "currency": g.currency,
                "expires_at": g.expires_at.isoformat(),
            }
            for g in soon
        ],
    }


def send_daily_reports(session: Session, now: datetime | None = None) -> int:
    """One `ops.exposure_report` per organisation that has provisional activity."""
    now = now or datetime.now(UTC)
    org_ids = session.scalars(
        select(ProvisionalGrant.org_id)
        .where(
            (ProvisionalGrant.status == ACTIVE)
            | (ProvisionalGrant.granted_at >= now - timedelta(days=7))
        )
        .distinct()
    ).all()
    sent = 0
    for org_id in org_ids:
        report = exposure_report(session, org_id, now)
        exposure = ", ".join(
            f"{v} {c} (minor units)" for c, v in report["active_exposure_minor"].items()
        )
        week = report["last_7_days"]
        queued = enqueue_event(
            session,
            org_id,
            "ops.exposure_report",
            f"exposure:{org_id}:{now:%Y-%m-%d}",
            {
                "text": "Provisional access report: "
                f"active exposure {exposure or 'none'}; last 7 days {week['granted']} granted, "
                f"{week['confirmed']} confirmed, {week['revoked']} revoked, "
                f"{week['expired']} expired.",
                "report": report,
            },
        )
        sent += int(queued)
    return sent


def access_state(session: Session, obligation: Obligation) -> dict[str, Any]:
    """What a host asks at checkout: is this obligation paid, provisionally open, or neither."""
    bal = ledger.balance(session, obligation)
    now = datetime.now(UTC)
    grants = session.scalars(
        select(ProvisionalGrant).where(
            ProvisionalGrant.obligation_id == obligation.obligation_id,
            ProvisionalGrant.status == ACTIVE,
            ProvisionalGrant.expires_at > now,
        )
    ).all()
    out: dict[str, Any] = {
        "obligation_id": str(obligation.obligation_id),
        "paid_minor": bal.paid_minor,
        "outstanding_minor": bal.outstanding_minor,
        "currency": obligation.currency,
        "payment_status": bal.status,
    }
    if bal.status == "paid":
        out["state"] = "confirmed"
    elif grants:
        out["state"] = "provisional"
        out["expires_at"] = max(g.expires_at for g in grants).isoformat()
        out["exposure_minor"] = sum(g.exposure_minor for g in grants)
    else:
        out["state"] = "none"
    return out
