"""Run one lookup for a claim and record it as evidence. Never changes the claim."""

from sqlalchemy.orm import Session

from app.claims.facts import build_facts
from app.core.audit import audit
from app.core.config import get_settings
from app.lookups import guard
from app.lookups.backends import Backend, get_backend
from app.lookups.schema import LookupUnavailable
from app.models import Claim, Obligation

ACTOR = "lookup"


class LookupError_(Exception):  # noqa: N818
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def run(
    session: Session, claim: Claim, url: str, requested_by: str, backend: Backend | None = None
) -> dict:
    s = get_settings()
    if s.lookup_disabled or s.lookup_mode == "off":
        raise LookupError_("LOOKUPS_OFF", "Provider lookups are switched off.")
    if not guard.url_allowed(url, s.lookup_allowed_domains):
        raise LookupError_("DOMAIN_NOT_ALLOWED", "That address is not on the allowed list.", 422)
    obligation = session.get(Obligation, claim.obligation_id)
    assert obligation is not None
    facts, _ = build_facts(session, claim, obligation)
    try:
        obs = (backend or get_backend()).observe(url)
    except LookupUnavailable as exc:
        audit(
            session,
            actor=ACTOR,
            action="claim.provider_lookup_failed",
            entity_type="claim",
            entity_id=claim.claim_id,
            org_id=claim.org_id,
            after={"requested_by": requested_by, "reason": str(exc)},
            correlation_id=claim.correlation_id,
        )
        return {"verdict": guard.INCONCLUSIVE, "observation": None, "recorded": True}
    verdict = guard.compare(obs, facts)
    audit(
        session,
        actor=ACTOR,
        action="claim.provider_lookup",
        entity_type="claim",
        entity_id=claim.claim_id,
        org_id=claim.org_id,
        after={
            "requested_by": requested_by,
            "mode": s.lookup_mode,
            "verdict": verdict,
            "observation": obs.model_dump(mode="json"),
            "decision_effect": "none",  # evidence for a person only
        },
        correlation_id=claim.correlation_id,
    )
    return {"verdict": verdict, "observation": obs.model_dump(mode="json"), "recorded": True}
