"""Reviewer reprocess, duplicate-hold release and the run history."""

from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _obligation, _submit


def _waiting_claim(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _submit(
        client,
        sub,
        ob,
        make_receipt(ecocash_fields()),
        declared_amount="40.00",
        declared_reference=TXN,
    )
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    return sub, claim


def test_reprocess_runs_again_and_keeps_history(client):
    sub, claim = _waiting_claim(client)
    rev = login(client, "reviewer")
    cid = claim["claim_id"]
    assert len(client.get(f"/review/claims/{cid}/runs", headers=rev).json()) == 1
    r = client.post(f"/review/claims/{cid}/reprocess", headers=rev, json={"note": "re-read please"})
    assert r.status_code == 200, r.text
    runs = client.get(f"/review/claims/{cid}/runs", headers=rev).json()
    assert len(runs) == 2
    assert (
        client.get(f"/claims/{cid}", headers=sub).json()["state"]
        == "AWAITING_FINANCIAL_CONFIRMATION"
    )


def test_reprocess_refuses_when_corrections_would_lapse_unless_confirmed(client):
    _, claim = _waiting_claim(client)
    rev = login(client, "reviewer")
    cid = claim["claim_id"]
    fix = client.post(
        f"/review/claims/{cid}/fields",
        headers=rev,
        json={"name": "transaction_id", "value": TXN, "reason": "checked at source"},
    )
    assert fix.status_code == 200, fix.text
    r = client.post(f"/review/claims/{cid}/reprocess", headers=rev, json={"note": "again"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CORRECTIONS_WOULD_LAPSE"
    ok = client.post(
        f"/review/claims/{cid}/reprocess",
        headers=rev,
        json={"note": "again", "discard_corrections": True},
    )
    assert ok.status_code == 200, ok.text


def test_reprocess_is_refused_for_rejected_claims(client):
    _, claim = _waiting_claim(client)
    rev = login(client, "reviewer")
    cid = claim["claim_id"]
    assert (
        client.post(
            f"/review/claims/{cid}/reject", headers=rev, json={"reason": "OTHER", "note": None}
        ).status_code
        == 200
    )
    r = client.post(f"/review/claims/{cid}/reprocess", headers=rev, json={"note": "again"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NOT_REPROCESSABLE"


def test_release_hold_needs_an_approver_and_a_held_claim(client):
    sub, claim = _waiting_claim(client)
    rev, appr = login(client, "reviewer"), login(client, "approver")
    body = {"note": "different payer, same bank screenshot template"}
    not_held = client.post(
        f"/review/claims/{claim['claim_id']}/release-hold", headers=appr, json=body
    )
    assert not_held.status_code == 409
    assert not_held.json()["detail"]["code"] == "NOT_ON_HOLD"

    ob = _obligation(client, sub)
    png = make_receipt(ecocash_fields())
    _submit(client, sub, ob, png)
    held = _submit(client, sub, ob, png)
    assert held["state"] == "DUPLICATE_HOLD"
    hid = held["claim_id"]
    assert (
        client.post(f"/review/claims/{hid}/release-hold", headers=rev, json=body).status_code == 403
    )
    ok = client.post(f"/review/claims/{hid}/release-hold", headers=appr, json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "PENDING_REVIEW"
    assert "DUPLICATE_HOLD_RELEASED" in ok.json()["reason_codes"]
    assert "DUPLICATE_FILE" in ok.json()["reason_codes"]  # the reviewer still sees why it was held
