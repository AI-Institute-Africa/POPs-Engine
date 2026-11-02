"""Provisional access: capped, time-boxed, reported to the host, ended when the money is settled."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.entitlements import outbox
from app.models import Claim, OutboxEvent, ProvisionalGrant
from app.policy.engine import PolicyConfig
from app.provisional.service import grant_if_eligible
from app.workers import tasks
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit

LAST4 = TXN[-4:]


def _enable(client, **cfg) -> None:
    r = client.post(
        "/admin/policy",
        headers=login(client, "admin"),
        json={
            "version": f"prov-{uuid.uuid4().hex[:8]}",
            "config": {"provisional_enabled": True, **cfg},
        },
    )
    assert r.status_code == 201, r.text


def _events(session, event_type: str) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at)
        )
    )


def _grants(session) -> list[ProvisionalGrant]:
    session.expire_all()
    return list(session.scalars(select(ProvisionalGrant).order_by(ProvisionalGrant.granted_at)))


def _access(client, h, ob) -> dict:
    r = client.get(f"/obligations/{ob['obligation_id']}/access", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _receipt_claim(client, sub, ob, **fields) -> dict:
    return _submit(client, sub, ob, make_receipt(ecocash_fields(**fields)))


def test_it_is_off_by_default(client, session):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _receipt_claim(client, sub, ob)
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    assert claim["provisional_until"] is None
    assert _grants(session) == [] and _events(session, "entitlement.provisional") == []
    assert _access(client, sub, ob)["state"] == "none"


def test_a_clean_receipt_gets_capped_time_boxed_access(client, session):
    _enable(client)
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _receipt_claim(client, sub, ob)

    (grant,) = _grants(session)
    assert grant.status == "active" and grant.exposure_minor == 4000
    assert timedelta(hours=23) < grant.expires_at - grant.granted_at <= timedelta(hours=24)

    assert claim["provisional_until"] is not None
    assert any("Access is open until" in m for m in claim["messages"])
    assert "fake" not in " ".join(claim["messages"]).lower()  # neutral wording only

    access = _access(client, sub, ob)
    assert access["state"] == "provisional" and access["exposure_minor"] == 4000
    assert access["outstanding_minor"] == 4000 and access["expires_at"]

    (event,) = _events(session, "entitlement.provisional")
    p = event.payload
    assert p["provisional"] is True and p["cause"] == "provisional_granted"
    assert p["obligation_id"] == ob["obligation_id"] and p["claim_id"] == claim["claim_id"]
    assert p["exposure_minor"] == 4000 and p["expires_at"] and p["payment_status"] == "unpaid"
    assert "learner-1" in p["learner_ref"]


def test_the_host_receives_signed_events(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "cognify_webhook_url", "https://host.test/hook")
    _enable(client)
    sub = login(client, "submitter")
    _receipt_claim(client, sub, _obligation(client, sub))
    sent: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200))
    )
    outbox.deliver_pending(session, client=http)
    bodies = [r.read().decode() for r in sent]
    assert any('"type":"entitlement.provisional"' in b for b in bodies)
    assert all(r.headers["X-POPs-Signature"].startswith("t=") for r in sent)


def test_approval_confirms_the_grant_without_revoking_it(client, session):
    _enable(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _receipt_claim(client, sub, ob)
    _import(client, rev, [_stmt_row()])
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    cid = detail["candidates"][0]["credit"]["credit_id"]
    done = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": cid, "txn_last4": LAST4},
    )
    assert done.status_code == 200 and done.json()["claim"]["state"] == "APPROVED"

    (grant,) = _grants(session)
    assert grant.status == "confirmed" and grant.resolved_reason == "approved"
    assert _events(session, "entitlement.revoked") == []
    assert _access(client, sub, ob)["state"] == "confirmed"
    assert session.get(Claim, uuid.UUID(claim["claim_id"])).provisional_until is None


def test_rejection_revokes_access_and_tells_the_host(client, session):
    _enable(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _receipt_claim(client, sub, ob)
    r = client.post(
        f"/review/claims/{claim['claim_id']}/reject",
        headers=rev,
        json={"reason": "PAYMENT_NOT_RECEIVED"},
    )
    assert r.status_code == 200, r.text
    (grant,) = _grants(session)
    assert grant.status == "revoked" and grant.resolved_reason == "rejected"
    (event,) = _events(session, "entitlement.revoked")
    assert event.payload["revoked_cause"] == "rejected" and event.payload["provisional"] is True
    assert _access(client, sub, ob)["state"] == "none"


def test_access_expires_when_no_payment_appears(client, session):
    _enable(client)
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _receipt_claim(client, sub, ob)
    assert tasks.run_expire_provisional() == 0  # still inside the window

    with get_sessionmaker()() as s:
        s.execute(
            update(ProvisionalGrant).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        s.commit()
    assert _access(client, sub, ob)["state"] == "none"  # an overdue grant never counts
    assert tasks.run_expire_provisional() == 1
    assert tasks.run_expire_provisional() == 0

    (grant,) = _grants(session)
    assert grant.status == "expired"
    assert [e.payload["revoked_cause"] for e in _events(session, "entitlement.revoked")] == [
        "expired"
    ]
    (ops,) = _events(session, "ops.provisional_expired")
    assert ops.payload["claim_id"] == claim["claim_id"] and "learner" not in ops.payload["text"]
    assert session.get(Claim, uuid.UUID(claim["claim_id"])).provisional_until is None


def test_a_suspected_fake_loses_access_immediately(client, session):
    _enable(
        client,
        provisional_max_minor={"USD": 50000},
        provisional_learner_cap_minor={"USD": 50000},
        provisional_org_daily_cap_minor={"USD": 50000},
    )
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub, amount=40000)
    _receipt_claim(client, sub, ob, amount="400.00")
    assert [g.status for g in _grants(session)] == ["active"]
    _import(client, rev, [_stmt_row()])  # the bank shows USD 40.00 for this reference
    (grant,) = _grants(session)
    assert grant.status == "revoked" and grant.resolved_reason == "suspected_fake"
    assert [e.payload["revoked_cause"] for e in _events(session, "entitlement.revoked")] == [
        "suspected_fake"
    ]


@pytest.mark.parametrize(
    "setup",
    ["over_claim_cap", "no_cap_for_currency", "risky_file", "duplicate", "typed_amount_differs"],
)
def test_ineligible_claims_get_no_access(client, session, setup):
    if setup == "over_claim_cap":
        _enable(client, provisional_max_minor={"USD": 3000})
    else:
        _enable(client)
    sub = login(client, "submitter")
    if setup == "no_cap_for_currency":
        ob = _obligation(client, sub, currency="ZWG")
        _submit(client, sub, ob, make_receipt(ecocash_fields(currency="ZWG")))
    elif setup == "risky_file":
        ob = _obligation(client, sub)
        _submit(client, sub, ob, make_receipt(ecocash_fields(), meta={"Software": "Canva"}))
    elif setup == "duplicate":
        ob = _obligation(client, sub)
        png = make_receipt(ecocash_fields())
        _submit(client, sub, ob, png)
        _submit(client, sub, ob, png)  # the second is held as a duplicate
        assert len(_grants(session)) == 1  # only the first
        return
    elif setup == "typed_amount_differs":
        ob = _obligation(client, sub)
        _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="99.00")
    else:
        ob = _obligation(client, sub)
        _receipt_claim(client, sub, ob)
    assert _grants(session) == [], setup
    assert _events(session, "entitlement.provisional") == []


def test_per_learner_cap_limits_total_open_access(client, session):
    _enable(client, provisional_learner_cap_minor={"USD": 6000})
    sub = login(client, "submitter")
    first = _receipt_claim(client, sub, _obligation(client, sub), txn="PP251021.1432.AAAA1")
    second = _receipt_claim(client, sub, _obligation(client, sub), txn="PP251021.1432.BBBB2")
    assert first["provisional_until"] is not None
    assert (
        second["provisional_until"] is None and second["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    )
    assert len(_grants(session)) == 1


def test_org_daily_cap_limits_total_new_access(client, session):
    _enable(client, provisional_org_daily_cap_minor={"USD": 6000})
    sub = login(client, "submitter")

    def other_learner(name: str) -> dict:
        r = client.post(
            "/obligations",
            headers=sub,
            json={
                "learner_ref": name,
                "type": "instalment",
                "amount_minor": 4000,
                "currency": "USD",
                "description": "x",
            },
        )
        return r.json()

    a = _receipt_claim(client, sub, other_learner("amy"), txn="PP251021.1432.AAAA1")
    b = _receipt_claim(client, sub, other_learner("ben"), txn="PP251021.1432.BBBB2")
    assert a["provisional_until"] is not None and b["provisional_until"] is None


def test_a_claim_holds_at_most_one_active_grant(client, session):
    _enable(client)
    sub = login(client, "submitter")
    claim = _receipt_claim(client, sub, _obligation(client, sub))
    row = session.get(Claim, uuid.UUID(claim["claim_id"]))
    again = grant_if_eligible(session, row)
    assert again is not None and len(_grants(session)) == 1


def test_exposure_report_for_admins(client, session):
    _enable(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    _receipt_claim(client, sub, _obligation(client, sub))
    assert client.get("/admin/exposure", headers=rev).status_code == 403
    r = client.get("/admin/exposure", headers=login(client, "admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["active_exposure_minor"] == {"USD": 4000}
    assert body["last_7_days"]["granted"] == 1 and body["last_7_days"]["active"] == 1
    assert body["caps"]["ttl_hours"] == 24 and body["caps"]["per_claim_minor"]["USD"] == 5000
    assert body["expiring_soon"] == []

    assert tasks.run_exposure_report() == 1
    assert tasks.run_exposure_report() == 0  # one per organisation per day
    (event,) = _events(session, "ops.exposure_report")
    assert "active exposure" in event.payload["text"] and event.payload["report"]["enabled"]


def test_access_endpoint_is_scoped_to_the_organisation(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    stranger = login(client, "submitter", org="Other")
    assert (
        client.get(f"/obligations/{ob['obligation_id']}/access", headers=stranger).status_code
        == 404
    )


def test_policy_validation():
    assert PolicyConfig().provisional_enabled is False
    for bad in ({"provisional_max_minor": {"USD": 0}}, {"provisional_ttl_hours": 0}):
        with pytest.raises(ValidationError):
            PolicyConfig(**bad)
