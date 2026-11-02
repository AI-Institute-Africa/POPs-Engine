"""The whole chain, end to end through the HTTP API, with the webhooks the host and admins see.

Upload a proof -> admins and the host system are told what they should be told -> access is open
provisionally -> money arrives -> a reviewer (or the rule) approves -> the host hears the result.
"""

import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.entitlements import outbox
from app.workers import tasks
from tests.conftest import ecocash_fields, iso_z, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit
from tests.test_notifications import _backdate, _in_review

HOST = "https://host.test/hook"
ADMIN = "https://hooks.slack.test/x"
LAST4 = TXN[-4:]


@pytest.fixture
def wired(monkeypatch):
    """Host webhook and admin webhook configured; deliveries are captured, in order."""
    monkeypatch.setattr(get_settings(), "cognify_webhook_url", HOST)
    monkeypatch.setattr(get_settings(), "admin_alert_webhook_url", ADMIN)
    seen: dict[str, list[dict]] = {"host": [], "admin": []}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        if req.url.host == "host.test":
            assert req.headers["X-POPs-Signature"].startswith("t=")
            seen["host"].append(body)
        else:
            seen["admin"].append(body)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def deliver(session) -> dict[str, list[str]]:
        outbox.deliver_pending(session, client=client)
        return {k: [b["type"] for b in v] for k, v in seen.items()}

    deliver.seen = seen  # type: ignore[attr-defined]
    return deliver


def _policy(client, **cfg) -> None:
    r = client.post(
        "/admin/policy",
        headers=login(client, "admin"),
        json={"version": f"chain-{uuid.uuid4().hex[:8]}", "config": cfg},
    )
    assert r.status_code == 201, r.text


def _credit_id(client, rev, claim_id: str) -> str:
    detail = client.get(f"/review/claims/{claim_id}", headers=rev).json()
    return detail["candidates"][0]["credit"]["credit_id"]


def test_receipt_to_provisional_access_to_reviewer_approval(client, session, wired):
    _policy(client, provisional_enabled=True)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)

    # 1. the payer uploads a proof: it is read, nothing is confirmed, access opens provisionally
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION" and claim["provisional_until"]
    sent = wired(session)
    assert sent == {"host": ["entitlement.provisional"], "admin": []}  # no human is needed yet
    provisional = wired.seen["host"][0]["data"]
    assert provisional["provisional"] is True and provisional["claim_id"] == claim["claim_id"]
    access = client.get(f"/obligations/{ob['obligation_id']}/access", headers=sub).json()
    assert access["state"] == "provisional" and access["exposure_minor"] == 4000

    # 2. the money shows up on our side: the claim waits for a person (the rule is off)
    _import(client, rev, [_stmt_row()])
    detail = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert detail["state"] == "FINANCIALLY_CONFIRMED"
    assert any(
        i["claim_id"] == claim["claim_id"] for i in client.get("/review/queue", headers=rev).json()
    )

    # 3. a reviewer confirms by typing the last characters of the bank reference
    done = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": _credit_id(client, rev, claim["claim_id"]), "txn_last4": LAST4},
    )
    assert done.status_code == 200 and done.json()["claim"]["state"] == "APPROVED"

    # 4. the host hears the final result; provisional access became confirmed, not revoked
    sent = wired(session)
    assert sent["host"] == ["entitlement.provisional", "entitlement.updated"]
    final = wired.seen["host"][1]["data"]
    assert final["payment_status"] == "paid" and final["paid_minor"] == 4000
    assert (
        client.get(f"/obligations/{ob['obligation_id']}/access", headers=sub).json()["state"]
        == "confirmed"
    )


def test_receipt_to_auto_approval_with_no_person(client, session, wired):
    _policy(
        client,
        provisional_enabled=True,
        auto_approve_enabled=True,
        auto_approve_channels=["ecocash"],
        auto_approve_max_minor={"USD": 5000},
    )
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert wired(session)["host"] == ["entitlement.provisional"]

    _import(client, rev, [_stmt_row()])  # the statement line is what lets the rule approve
    assert client.get(f"/claims/{claim['claim_id']}", headers=sub).json()["state"] == "APPROVED"
    sent = wired(session)
    assert sent["host"] == ["entitlement.provisional", "entitlement.updated"]
    assert sent["admin"] == []  # nothing needed a person
    auto = client.get("/review/auto-approved", headers=rev).json()
    assert auto["summary"]["approved"] == 1 and auto["claims"][0]["claim_id"] == claim["claim_id"]


def test_a_doubtful_claim_pages_admins_and_gets_no_provisional_access(client, session, wired):
    _policy(client, provisional_enabled=True)
    claim = _in_review(client)  # the readers disagree on the amount
    sent = wired(session)
    assert sent["host"] == [] and sent["admin"] == ["ops.review_pending"]
    assert wired.seen["admin"][0]["data"]["claim_id"] == claim["claim_id"]
    assert "provisional_until" in claim and claim["provisional_until"] is None

    _backdate(claim["claim_id"], 90)  # nobody picked it up for an hour and a half
    assert tasks.run_review_aging() == 1
    assert wired(session)["admin"] == ["ops.review_pending", "ops.review_overdue"]


def test_money_with_no_claim_is_recorded_then_allocated_by_a_second_person(client, session, wired):
    sub = login(client, "submitter")
    first = login(client, "approver", email="first@cognify.test")
    second = login(client, "approver", email="second@cognify.test")
    ob = _obligation(client, sub)

    r = client.post(
        "/credits",
        headers=first,
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
    credit_id = r.json()["credit_id"]
    assert [
        u["credit"]["credit_id"] for u in client.get("/credits/unclaimed", headers=first).json()
    ] == [credit_id]

    body = {"obligation_id": ob["obligation_id"], "txn_last4": LAST4}
    assert (
        client.post(f"/credits/{credit_id}/allocate", headers=first, json=body).status_code == 403
    )
    done = client.post(f"/credits/{credit_id}/allocate", headers=second, json=body)
    assert done.status_code == 200 and done.json()["payment_status"] == "paid"

    assert wired(session)["host"] == ["entitlement.updated"]
    assert wired.seen["host"][0]["data"]["paid_minor"] == 4000
    assert client.get("/credits/unclaimed", headers=first).json() == []
    assert (
        client.get(f"/obligations/{ob['obligation_id']}/access", headers=sub).json()["state"]
        == "confirmed"
    )
