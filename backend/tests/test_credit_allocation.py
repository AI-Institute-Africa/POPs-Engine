"""Admin allocation of a recorded, unclaimed credit to an obligation (no payer claim)."""

import uuid

from sqlalchemy import select

from app.models import AuditEvent, OutboxEvent
from tests.conftest import ecocash_fields, iso_z, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit

LAST4 = TXN[-4:]


def _credit(client, rev, amount="40.00", txn=TXN) -> str:
    _import(client, rev, [_stmt_row(txn=txn, amount=amount)])
    rows = client.get("/credits", headers=rev).json()
    return next(c["credit_id"] for c in rows if c["provider_txn_id"] == txn)


def _allocate(client, h, credit_id, ob, **body):
    return client.post(
        f"/credits/{credit_id}/allocate",
        headers=h,
        json={"obligation_id": ob["obligation_id"], "txn_last4": LAST4, **body},
    )


def _paid(client, h, ob) -> int:
    return client.get(f"/obligations/{ob['obligation_id']}/balance", headers=h).json()["paid_minor"]


def test_admin_allocates_an_unclaimed_credit(client, session):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    r = _allocate(client, appr, cid, ob, note="payer phoned finance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allocation"]["amount_minor"] == 4000 and body["allocation"]["claim_id"] is None
    assert body["payment_status"] == "paid" and body["outstanding_minor"] == 0
    assert _paid(client, sub, ob) == 4000

    credit = next(c for c in client.get("/credits", headers=rev).json() if c["credit_id"] == cid)
    assert credit["available_minor"] == 0
    assert [c for c in client.get("/credits/unclaimed", headers=rev).json()] == []

    session.expire_all()
    audits = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "allocation.admin_created")
    )
    assert [a.after["note"] for a in audits] == ["payer phoned finance"]
    events = session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == "entitlement.updated")
    ).all()
    assert len(events) == 1 and events[0].payload["cause"] == "allocation"
    assert events[0].payload["payment_status"] == "paid"


def test_partial_allocation_leaves_the_rest_available(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    r = _allocate(client, appr, cid, ob, amount_minor=1500)
    assert r.status_code == 200 and r.json()["payment_status"] == "partially_paid"
    assert r.json()["outstanding_minor"] == 2500
    credit = next(c for c in client.get("/credits", headers=rev).json() if c["credit_id"] == cid)
    assert credit["available_minor"] == 2500


def test_retry_with_the_same_request_id_does_not_allocate_twice(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    rid = str(uuid.uuid4())
    first = _allocate(client, appr, cid, ob, amount_minor=1500, request_id=rid)
    again = _allocate(client, appr, cid, ob, amount_minor=1500, request_id=rid)
    assert first.status_code == again.status_code == 200
    assert (
        first.json()["allocation"]["allocation_id"] == again.json()["allocation"]["allocation_id"]
    )
    assert _paid(client, sub, ob) == 1500


def test_wrong_last_characters_are_refused(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    r = _allocate(client, appr, cid, ob, txn_last4="ZZZZ")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "LAST4_MISMATCH"
    assert _paid(client, sub, ob) == 0


def test_a_reviewer_cannot_allocate_under_the_default_policy(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    assert _allocate(client, rev, cid, ob).status_code == 403
    assert _allocate(client, sub, cid, ob).status_code == 403  # submitters never reach review APIs


def test_me_reports_what_the_caller_may_do(client):
    rev, appr = login(client, "reviewer"), login(client, "approver")
    r = client.get("/me", headers=rev).json()
    assert r["can_allocate"] is False and r["can_record_manual_credit"] is False
    a = client.get("/me", headers=appr).json()
    assert a["can_allocate"] is True and a["can_record_manual_credit"] is True


def test_nobody_allocates_a_manual_credit_they_recorded(client):
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
    cid = r.json()["credit_id"]
    own = _allocate(client, first, cid, ob)
    assert own.status_code == 403 and own.json()["detail"]["code"] == "SEPARATION_OF_DUTIES"
    assert _allocate(client, second, cid, ob).status_code == 200


def test_high_value_allocation_needs_two_different_approvers(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    first = login(client, "approver", email="first@cognify.test")
    second = login(client, "approver", email="second@cognify.test")
    ob = _obligation(client, sub, amount=120000)
    cid = _credit(client, rev, amount="1200.00")

    one = _allocate(client, first, cid, ob)
    assert one.status_code == 200 and one.json()["needs_second_approval"] is True
    assert one.json()["allocation"] is None and _paid(client, sub, ob) == 0

    same = _allocate(client, first, cid, ob)
    assert same.status_code == 409 and same.json()["detail"]["code"] == "SECOND_APPROVER_REQUIRED"

    other_amount = _allocate(client, second, cid, ob, amount_minor=100000)
    assert other_amount.json()["needs_second_approval"] is True  # a different request, new first

    done = _allocate(client, second, cid, ob)
    assert done.status_code == 200 and done.json()["allocation"]["amount_minor"] == 120000
    assert done.json()["needs_second_approval"] is False
    assert _paid(client, sub, ob) == 120000


def test_cannot_allocate_more_than_is_allowed(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    r = _allocate(client, appr, cid, ob, amount_minor=5000)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "AMOUNT_EXCEEDS"
    assert _allocate(client, appr, cid, ob).status_code == 200
    again = _allocate(client, appr, cid, ob)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "NOTHING_TO_ALLOCATE"


def test_currency_must_match(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub, currency="ZWG")
    cid = _credit(client, rev)
    r = _allocate(client, appr, cid, ob)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CURRENCY_MISMATCH"


def test_a_waiting_claim_must_be_confirmed_from_the_queue_instead(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    cid = _credit(client, rev, txn="OTHER-WXYZ")
    r = _allocate(client, appr, cid, ob, txn_last4="WXYZ")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CLAIM_WAITING"


def test_other_organisations_cannot_see_the_credit_or_obligation(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    stranger = login(client, "approver", org="Other")
    assert _allocate(client, stranger, cid, ob).status_code == 404


def test_an_admin_allocation_can_be_reversed(client, session):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _obligation(client, sub)
    cid = _credit(client, rev)
    alloc = _allocate(client, appr, cid, ob).json()["allocation"]
    r = client.post(
        f"/allocations/{alloc['allocation_id']}/reverse",
        headers=appr,
        json={"reason": "allocated to the wrong learner"},
    )
    assert r.status_code == 200, r.text
    assert _paid(client, sub, ob) == 0
    session.expire_all()
    kinds = [
        e.payload["payment_status"]
        for e in session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == "entitlement.updated")
            .order_by(OutboxEvent.created_at)
        )
    ]
    assert kinds == ["paid", "unpaid"]
