"""Provider lookups are evidence for a person: never a decision, and they fail closed."""

import json
from urllib.parse import quote

import pytest

from app.core.config import get_settings
from app.lookups import guard
from app.lookups.backends import BrowserBackend
from app.lookups.schema import LookupUnavailable, ProviderObservation
from app.normalise.fields import normalise_reference
from app.reconcile.matching import ClaimFacts
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _obligation, _submit


def _facts(**kw):
    base = dict(
        org_id=None, amount_minor=4000, currency="USD", reference=normalise_reference(TXN),
        occurred_at=None, sender=None, payment_code="X",
    )  # fmt: skip
    return ClaimFacts(**{**base, **kw})


def _obs(**kw):
    base = dict(found=True, txn_id=TXN, amount_minor=4000, currency="USD", status="Successful")
    return ProviderObservation(**{**base, **kw})


def test_domain_allow_list():
    ok = ["ecocash.co.zw"]
    assert guard.url_allowed("https://ecocash.co.zw/r/1", ok)
    assert guard.url_allowed("https://pay.ecocash.co.zw/r/1", ok)
    for bad in (
        "http://ecocash.co.zw/r/1",
        "https://evil-ecocash.co.zw/r",
        "https://ecocash.co.zw.evil.com/r",
        "https://user:pw@ecocash.co.zw/r",
        "https://10.0.0.1/r",
        "https://127.0.0.1/r",
    ):
        assert not guard.url_allowed(bad, ok), bad


def test_compare_verdicts():
    assert guard.compare(_obs(), _facts()) == guard.CONFIRMS
    assert guard.compare(_obs(amount_minor=100), _facts()) == guard.CONTRADICTS
    assert guard.compare(_obs(status="Failed"), _facts()) == guard.CONTRADICTS
    # not finding it, a different transaction, or a page that gave orders prove nothing
    assert guard.compare(_obs(found=False), _facts()) == guard.INCONCLUSIVE
    assert guard.compare(_obs(txn_id="OTHER"), _facts()) == guard.INCONCLUSIVE
    assert guard.compare(_obs(injection_suspected=True), _facts()) == guard.INCONCLUSIVE


def test_missing_browser_library_fails_closed():
    with pytest.raises(LookupUnavailable):
        BrowserBackend().observe("https://ecocash.co.zw/r/1")


def _setup(client, monkeypatch, mode="shadow"):
    s = get_settings()
    monkeypatch.setattr(s, "lookup_mode", mode)
    monkeypatch.setattr(s, "lookup_backend", "fake")
    monkeypatch.setattr(s, "lookup_allowed_domains", ["ecocash.co.zw"])
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")
    return sub, rev, claim


_OK = dict(found=True, txn_id=TXN, amount_minor=4000, currency="USD", status="Successful")


def _url(**obs):
    return "https://ecocash.co.zw/r/1?obs=" + quote(json.dumps(obs))


def test_lookup_is_recorded_and_the_claim_is_untouched(client, monkeypatch):
    sub, rev, claim = _setup(client, monkeypatch)
    before = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    r = client.post(
        f"/claims/{claim['claim_id']}/provider-lookup",
        headers=rev,
        json={
            "url": _url(
                found=True, txn_id=TXN, amount_minor=4000, currency="USD", status="Successful"
            )
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "CONFIRMS"
    after = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    for k in ("state", "approval_tier", "reason_codes", "evidence_tier"):
        assert after[k] == before[k], k
    trail = client.get(f"/audit?entity_id={claim['claim_id']}", headers=rev).json()
    ev = next(e for e in trail if e["action"] == "claim.provider_lookup")
    assert ev["after"]["decision_effect"] == "none" and ev["after"]["verdict"] == "CONFIRMS"


def test_lookup_refused_when_off_disallowed_or_killed(client, monkeypatch):
    _, rev, claim = _setup(client, monkeypatch, mode="off")
    path = f"/claims/{claim['claim_id']}/provider-lookup"
    assert client.post(path, headers=rev, json={"url": _url(found=False)}).status_code == 409
    monkeypatch.setattr(get_settings(), "lookup_mode", "shadow")
    r = client.post(path, headers=rev, json={"url": "https://evil.example/r?obs=%7B%7D"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "DOMAIN_NOT_ALLOWED"
    monkeypatch.setattr(get_settings(), "lookup_disabled", True)
    assert client.post(path, headers=rev, json={"url": _url(found=False)}).status_code == 409


def test_unreadable_page_records_failure_and_changes_nothing(client, monkeypatch):
    sub, rev, claim = _setup(client, monkeypatch)
    r = client.post(
        f"/claims/{claim['claim_id']}/provider-lookup",
        headers=rev,
        json={"url": "https://ecocash.co.zw/r/1"},  # nothing for the fake backend to read
    )
    assert r.status_code == 200 and r.json()["verdict"] == "INCONCLUSIVE"
    trail = client.get(f"/audit?entity_id={claim['claim_id']}", headers=rev).json()
    assert any(e["action"] == "claim.provider_lookup_failed" for e in trail)


def test_submitters_cannot_run_lookups(client, monkeypatch):
    sub, _, claim = _setup(client, monkeypatch)
    r = client.post(
        f"/claims/{claim['claim_id']}/provider-lookup", headers=sub, json={"url": _url()}
    )
    assert r.status_code == 403
