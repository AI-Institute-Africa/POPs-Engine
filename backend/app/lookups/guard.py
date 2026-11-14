"""Pure checks: is this address allowed, and does what the page shows agree with the claim?"""

import ipaddress
from urllib.parse import urlparse

from app.lookups.schema import ProviderObservation
from app.normalise.fields import STATUS_SYNONYMS, normalise_reference
from app.reconcile.matching import ClaimFacts

CONFIRMS, CONTRADICTS, INCONCLUSIVE = "CONFIRMS", "CONTRADICTS", "INCONCLUSIVE"


def url_allowed(url: str, allowed_domains: list[str]) -> bool:
    """https only; no raw IPs or credentials; host equal to or under an allowed domain."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or p.username or p.password:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains)


def compare(obs: ProviderObservation, facts: ClaimFacts) -> str:
    """CONTRADICTS only on a clear mismatch; CONFIRMS needs reference, amount, status to agree."""
    if obs.injection_suspected:
        return INCONCLUSIVE  # never trust a page that tried to give orders
    if not obs.found:
        return INCONCLUSIVE  # not seeing it is not proof it is missing
    ref_ok = bool(facts.reference and normalise_reference(obs.txn_id) == facts.reference)
    amt_ok = obs.amount_minor is not None and obs.amount_minor == facts.amount_minor
    cur_ok = obs.currency is None or facts.currency is None or obs.currency == facts.currency
    ok_status = STATUS_SYNONYMS.get((obs.status or "").strip().lower()) == "success"
    if ref_ok and (not amt_ok or not cur_ok):
        return CONTRADICTS
    if ref_ok and not ok_status and obs.status:
        return CONTRADICTS
    if ref_ok and amt_ok and cur_ok and ok_status:
        return CONFIRMS
    return INCONCLUSIVE
