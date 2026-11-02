"""Rules-based approval of clear claims: when it fires, when it must not, and how it is audited."""

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.ledger import service as ledger
from app.models import Allocation, Decision, OutboxEvent
from app.review import auto
from tests.conftest import ecocash_fields, iso_z, login, make_receipt
from tests.test_alerts import ECOCASH_SMS, _sms, _source
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit

AUTO = {
    "auto_approve_enabled": True,
    "auto_approve_channels": ["ecocash"],
    "auto_approve_max_minor": {"USD": 5000},
}


def _policy(client, **cfg) -> None:
    r = client.post(
        "/admin/policy",
        headers=login(client, "admin"),
        json={"version": f"auto-{uuid.uuid4().hex[:8]}", "config": {**AUTO, **cfg}},
    )
    assert r.status_code == 201, r.text


def _claim(client, h, claim_id) -> dict:
    return client.get(f"/claims/{claim_id}", headers=h).json()


def _state_after_statement_first(client, *, amount="40.00", ob_minor=4000, png=None, **form):
    """Money is already on our books when the receipt arrives."""
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub, amount=ob_minor)
    _import(client, rev, [_stmt_row(amount=amount)])
    claim = _submit(client, sub, ob, png or make_receipt(ecocash_fields(amount=amount)), **form)
    return client.get(f"/claims/{claim['claim_id']}", headers=rev).json(), claim, ob


def test_off_by_default_a_confirmed_claim_waits_for_a_person(client):
    c, _, _ = _state_after_statement_first(client)
    assert c["state"] == "FINANCIALLY_CONFIRMED" and c["approval_tier"] == "GREEN"


def test_a_clear_claim_is_approved_with_no_person(client, session):
    _policy(client)
    c, claim, ob = _state_after_statement_first(client)
    assert c["state"] == "APPROVED" and c["evidence_tier"] == "E3"
    assert "AUTO_APPROVED" in c["reason_codes"]  # visible to finance…
    sub = login(client, "submitter")
    assert (
        "AUTO_APPROVED" not in _claim(client, sub, claim["claim_id"])["reason_codes"]
    )  # …not payers

    session.expire_all()
    allocs = session.scalars(select(Allocation)).all()
    assert [(a.amount_minor, a.created_by, str(a.claim_id)) for a in allocs] == [
        (4000, auto.AUTO_ACTOR, claim["claim_id"])
    ]
    approved = session.scalars(
        select(Decision).where(Decision.outcome == "APPROVED", Decision.actor_id == auto.AUTO_ACTOR)
    ).all()
    assert len(approved) == 1 and approved[0].actor_type == "rule"
    assert approved[0].evidence_snapshot["auto"] is True
    events = session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == "entitlement.updated")
    ).all()
    assert [e.payload["payment_status"] for e in events] == ["paid"]
    assert (
        client.get(
            f"/obligations/{ob['obligation_id']}/balance", headers=login(client, "reviewer")
        ).json()["outstanding_minor"]
        == 0
    )


def test_money_arriving_after_the_receipt_also_approves(client):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    r = _import(client, rev, [_stmt_row()])
    assert r.json()["rematched_claims"] == 1  # counted even though it was approved on the spot
    assert _claim(client, sub, claim["claim_id"])["state"] == "APPROVED"


def test_a_merchant_alert_is_enough_but_a_stricter_policy_asks_for_the_statement(client):
    _policy(client)  # default: E2 (alert) or better
    sub = login(client, "submitter")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    src = _source(client)
    assert _sms(client, src["key"], ECOCASH_SMS).json()["status"] == "credited"
    after = _claim(client, sub, claim["claim_id"])
    assert after["state"] == "APPROVED" and after["evidence_tier"] == "E2"


def test_a_stricter_policy_does_not_trust_an_alert_alone(client):
    _policy(client, auto_approve_min_tier="E3")
    sub = login(client, "submitter")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    _sms(client, _source(client)["key"], ECOCASH_SMS)
    assert _claim(client, sub, claim["claim_id"])["state"] == "FINANCIALLY_CONFIRMED"


@pytest.mark.parametrize(
    "case",
    [
        "channel_not_enabled",
        "over_cap",
        "high_value",
        "partial_payment",
        "risky_file",
        "kill_switch",
    ],
)
def test_claims_that_break_a_rule_stay_for_a_person(client, session, monkeypatch, case):
    kwargs: dict = {}
    cfg: dict = {}
    if case == "channel_not_enabled":
        cfg = {"auto_approve_channels": ["bank_transfer"]}
    elif case == "over_cap":
        kwargs = {"amount": "60.00", "ob_minor": 6000}
    elif case == "high_value":
        cfg = {"second_approver_above_minor": {"USD": 3000}}
    elif case == "partial_payment":
        kwargs = {"ob_minor": 10000}  # USD 40 paid against a USD 100 invoice
    elif case == "risky_file":
        kwargs = {"png": make_receipt(ecocash_fields(), meta={"Software": "Canva"})}
    elif case == "kill_switch":
        monkeypatch.setattr(get_settings(), "auto_approve_disabled", True)
    _policy(client, **cfg)
    c, _, _ = _state_after_statement_first(client, **kwargs)
    assert c["state"] == "FINANCIALLY_CONFIRMED", case
    session.expire_all()
    assert session.scalars(select(Allocation)).all() == []


def test_a_suspected_fake_is_never_approved_by_the_rule(client):
    _policy(client, auto_approve_max_minor={"USD": 50000})
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub, amount=40000)
    _import(client, rev, [_stmt_row()])  # the bank says USD 40.00 for this reference
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(amount="400.00")))
    c = client.get(f"/claims/{claim['claim_id']}", headers=rev).json()
    assert c["state"] != "APPROVED" and "SUSPECTED_FAKE" in c["reason_codes"]


def test_a_manual_credit_still_needs_a_second_person(client):
    _policy(client)
    sub, appr = login(client, "submitter"), login(client, "approver")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    r = client.post(
        "/credits",
        headers=appr,
        json={
            "provider": "ECOCASH",
            "provider_txn_id": TXN,
            "amount_minor": 4000,
            "currency": "USD",
            "occurred_at": iso_z(),
            "beneficiary": "MAIN",
            "attestation": "Seen on merchant portal by finance officer at 15:00",
        },
    )
    assert r.status_code == 201, r.text
    assert _claim(client, sub, claim["claim_id"])["state"] == "FINANCIALLY_CONFIRMED"


def test_a_ledger_refusal_leaves_the_claim_for_a_person(client, session, monkeypatch):
    _policy(client)

    def refuse(*_a, **_k):
        raise ledger.LedgerError("CREDIT_FULLY_ALLOCATED", "no money left")

    monkeypatch.setattr(auto, "settle", refuse)
    c, _, _ = _state_after_statement_first(client)
    assert c["state"] == "FINANCIALLY_CONFIRMED"
    session.expire_all()
    assert session.scalars(select(Allocation)).all() == []


def test_rerunning_the_rule_on_an_approved_claim_does_nothing(client, session):
    _policy(client)
    _, claim, _ = _state_after_statement_first(client)
    from app.models import Claim

    row = session.get(Claim, uuid.UUID(claim["claim_id"]))
    assert auto.maybe_auto_approve(session, row) is False
    assert len(session.scalars(select(Allocation)).all()) == 1


def test_sampled_audit_and_overturn_rate(client, session):
    _policy(client, auto_approve_audit_pct=100)
    rev, appr = login(client, "reviewer"), login(client, "approver")
    _, claim, _ = _state_after_statement_first(client)
    listing = client.get("/review/auto-approved", headers=rev).json()
    assert listing["enabled"] and listing["summary"]["approved"] == 1
    assert listing["summary"]["sampled"] == 1 and listing["summary"]["overturn_rate"] is None
    (row,) = listing["claims"]
    assert row["claim_id"] == claim["claim_id"] and row["amount_minor"] == 4000 and row["sampled"]

    bad = client.post(
        f"/review/claims/{claim['claim_id']}/audit-verdict", headers=rev, json={"verdict": "maybe"}
    )
    assert bad.status_code == 422
    ok = client.post(
        f"/review/claims/{claim['claim_id']}/audit-verdict",
        headers=rev,
        json={"verdict": "overturned", "note": "paid by someone else"},
    )
    assert ok.status_code == 200 and ok.json()["outcome"] == "AUTO_AUDIT_OVERTURNED"
    again = client.post(
        f"/review/claims/{claim['claim_id']}/audit-verdict", headers=rev, json={"verdict": "ok"}
    )
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_AUDITED"

    summary = client.get("/review/auto-approved", headers=rev).json()["summary"]
    assert (
        summary["audited"] == 1 and summary["overturned"] == 1 and summary["overturn_rate"] == 1.0
    )

    # overturning records the verdict; reversing the money is a separate, approver-only step
    session.expire_all()
    alloc = session.scalars(select(Allocation)).one()
    r = client.post(
        f"/allocations/{alloc.allocation_id}/reverse",
        headers=appr,
        json={"reason": "audit overturned"},
    )
    assert r.status_code == 200, r.text


def test_audit_verdict_only_applies_to_automatic_approvals(client):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    r = client.post(
        f"/review/claims/{claim['claim_id']}/audit-verdict", headers=rev, json={"verdict": "ok"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_AUTO_APPROVED"


def test_the_sample_is_deterministic():
    cid = uuid.uuid4()
    assert auto.is_sampled(cid, 100) and not auto.is_sampled(cid, 0)
    assert auto.is_sampled(cid, 37) == auto.is_sampled(cid, 37)
    hits = sum(auto.is_sampled(uuid.uuid4(), 20) for _ in range(2000))
    assert 250 < hits < 550  # roughly 20 %


def test_policy_must_scope_auto_approval(client):
    admin = login(client, "admin")

    def post(cfg: dict):
        return client.post(
            "/admin/policy",
            headers=admin,
            json={"version": f"v-{uuid.uuid4().hex[:6]}", "config": cfg},
        )

    no_channels = post({"auto_approve_enabled": True})
    assert no_channels.status_code == 422 and "auto_approve_channels" in no_channels.text
    assert post({"auto_approve_min_tier": "E1"}).status_code == 422  # a receipt alone is not enough
    assert post({"auto_approve_max_minor": {"USD": 0}}).status_code == 422
    assert post({"auto_approve_max_risk": 101}).status_code == 422
    assert post(AUTO).status_code == 201


def test_replay_estimates_how_many_past_claims_would_be_approved(client):
    sub, rev, admin = (login(client, r) for r in ("submitter", "reviewer", "admin"))
    _import(client, rev, [_stmt_row()])
    _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    # second claim: same payment code situation but a channel the draft does not enable
    off = client.post("/admin/replay", headers=admin, json={"config": {}}).json()["auto_approve"]
    assert off["claims_with_a_match"] == 1 and off["would_auto_approve"] == 0
    assert off["blockers"]["DISABLED"] == 1

    on = client.post("/admin/replay", headers=admin, json={"config": AUTO}).json()["auto_approve"]
    assert on["would_auto_approve"] == 1 and on["blockers"] == {}

    capped = client.post(
        "/admin/replay",
        headers=admin,
        json={"config": {**AUTO, "auto_approve_max_minor": {"USD": 1000}}},
    ).json()["auto_approve"]
    assert capped["would_auto_approve"] == 0 and capped["blockers"] == {"OVER_CAP": 1}
