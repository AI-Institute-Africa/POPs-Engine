"""Replay a draft policy over past claims, and measure readers and fraud rules on outcomes."""

from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import _import, _obligation, _stmt_row, _submit


def test_draft_policy_shows_what_would_change_without_changing_anything(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    # money arrived 50 cents short (sender-paid fee); the current policy needs exact amounts
    _import(client, rev, [_stmt_row(amount="39.50")])
    short = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    edited = _submit(
        client,
        sub,
        _obligation(client, sub),
        make_receipt(ecocash_fields(txn="PP251021.1600.Z00001"), meta={"Software": "Snapseed"}),
    )

    current = client.post("/admin/replay", headers=rev, json={}).json()
    assert current["changes"] == {} and current["claims_replayed"] == 2

    draft = {"amount_tolerance_minor": 50, "fraud_weights": {"EDITING_SOFTWARE": 70}}
    r = client.post("/admin/replay", headers=rev, json={"config": draft}).json()
    assert r["policy_version"] == "draft"
    changed = {row["claim_id"]: row["changes"] for row in r["changed_claims"]}
    # the fee tolerance both matches the payment and clears the "amount edited" suspicion that
    # an exact-amount policy raises for a 50-cent fee shortfall
    assert changed[short["claim_id"]] == ["newly_matched", "risk_down"]
    assert changed[edited["claim_id"]] == ["newly_suspected_fake"]

    # replay is read-only
    after = client.get(f"/claims/{edited['claim_id']}", headers=rev).json()
    assert after["risk_level"] == "medium"
    assert (
        client.get(f"/claims/{short['claim_id']}", headers=rev).json()["state"]
        == "AWAITING_FINANCIAL_CONFIRMATION"
    )


def test_invalid_draft_is_refused(client):
    rev = login(client, "reviewer")
    r = client.post("/admin/replay", headers=rev, json={"config": {"date_window_hours": "soon"}})
    assert r.status_code == 422


def test_accuracy_uses_confirmed_payments_and_admin_decisions(client):
    sub, rev, admin = login(client, "submitter"), login(client, "reviewer"), login(client, "admin")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    cid = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()["candidates"][0][
        "credit"
    ]["credit_id"]
    client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": cid, "txn_last4": "2345"},
    )

    png = make_receipt(ecocash_fields(txn="PP251021.1700.R00002"))
    other = _obligation(client, sub)
    _submit(client, sub, other, png)
    _submit(client, sub, other, png)
    alert = client.get("/fraud/alerts", headers=admin).json()[0]
    client.post(
        f"/fraud/alerts/{alert['alert_id']}/resolve",
        headers=admin,
        json={"resolution": "confirmed_fake", "note": "same screenshot twice"},
    )

    acc = client.get("/admin/accuracy", headers=rev).json()
    readers = acc["readers"]
    assert readers["confirmed_claims"] == 1
    assert readers["by_reader"]["fake_vlm"]["amount"]["accuracy_pct"] == 100.0
    assert readers["by_reader"]["fake_ocr"]["transaction_id"]["accuracy_pct"] == 100.0
    group = next(iter(readers["by_channel"].values()))
    assert group["amount_accuracy_pct"] == 100.0 and group["auto_agreed_pct"] == 100.0

    fraud = acc["fraud"]
    assert fraud["confirmed_fakes"] == 1 and fraud["overall_precision_pct"] == 100.0
    assert fraud["by_signal"]["REUSED_RECEIPT"]["confirmed_fake"] == 1
