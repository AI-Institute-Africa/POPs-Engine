"""An obligation's ledger row by row, for a host system that mirrors it, and the external_ref
lookup that lets a host recover an obligation it created but lost track of."""

import uuid

from tests.conftest import login
from tests.test_flow import TXN, _import, _stmt_row

LAST4 = TXN[-4:]


def _ob(client, h, external_ref=None, amount=4000):
    body = {
        "learner_ref": "learner-1",
        "type": "instalment",
        "amount_minor": amount,
        "currency": "USD",
        "description": "Course instalment",
    }
    if external_ref:
        body["external_ref"] = external_ref
    r = client.post("/obligations", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _credit(client, rev, amount="100.00", txn=TXN) -> str:
    _import(client, rev, [_stmt_row(txn=txn, amount=amount)])
    rows = client.get("/credits", headers=rev).json()
    return next(c["credit_id"] for c in rows if c["provider_txn_id"] == txn)


def _allocate(client, appr, credit_id, ob, **body):
    return client.post(
        f"/credits/{credit_id}/allocate",
        headers=appr,
        json={
            "obligation_id": ob["obligation_id"],
            "txn_last4": LAST4,
            "request_id": str(uuid.uuid4()),
            **body,
        },
    )


def _ledger(client, h, ob):
    r = client.get(f"/obligations/{ob['obligation_id']}/allocations", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_an_untouched_obligation_has_an_empty_ledger(client):
    sub = login(client, "submitter")
    assert _ledger(client, sub, _ob(client, sub)) == []


def test_lists_allocations_and_reversals_oldest_first(client):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = _ob(client, sub)
    cid = _credit(client, rev)

    first = _allocate(client, appr, cid, ob, amount_minor=1500)
    assert first.status_code == 200, first.text
    alloc_id = first.json()["allocation"]["allocation_id"]

    rows = _ledger(client, sub, ob)  # a plain submitter token is enough
    assert [(r["kind"], r["amount_minor"]) for r in rows] == [("allocation", 1500)]
    row = rows[0]
    assert row["allocation_id"] == alloc_id and row["credit_id"] == cid
    assert row["provider_txn_id"] == TXN and row["currency"] == "USD"
    assert row["claim_id"] is None and row["reverses"] is None
    assert row["created_by"].startswith("user:") and row["credit_occurred_at"]

    undone = client.post(
        f"/allocations/{alloc_id}/reverse", headers=appr, json={"reason": "bank recalled it"}
    )
    assert undone.status_code == 200, undone.text
    second = _allocate(client, appr, cid, ob, amount_minor=1000)
    assert second.status_code == 200, second.text

    rows = _ledger(client, sub, ob)
    assert [(r["kind"], r["amount_minor"]) for r in rows] == [
        ("allocation", 1500),
        ("reversal", 1500),  # always positive: `kind` carries the direction
        ("allocation", 1000),
    ]
    assert rows[1]["reverses"] == alloc_id and rows[1]["reason"] == "bank recalled it"
    assert rows[2]["reverses"] is None
    assert len({r["allocation_id"] for r in rows}) == 3

    # the ledger agrees with the balance the host already reads
    bal = client.get(f"/obligations/{ob['obligation_id']}/balance", headers=sub).json()
    net = sum(r["amount_minor"] * (-1 if r["kind"] == "reversal" else 1) for r in rows)
    assert bal["paid_minor"] == net == 1000


def test_another_org_cannot_read_the_ledger(client):
    sub = login(client, "submitter")
    ob = _ob(client, sub)
    stranger = login(client, "submitter", org="Someone Else")
    url = f"/obligations/{ob['obligation_id']}/allocations"
    assert client.get(url, headers=stranger).status_code == 404
    assert client.get(url).status_code == 401
    assert client.get(f"/obligations/{uuid.uuid4()}/allocations", headers=sub).status_code == 404


def test_obligations_can_be_found_by_external_ref(client):
    sub = login(client, "submitter")
    a = _ob(client, sub, external_ref="enr:1:1")
    _ob(client, sub, external_ref="enr:1:2")

    found = client.get("/obligations", params={"external_ref": "enr:1:1"}, headers=sub).json()
    assert [r["obligation_id"] for r in found] == [a["obligation_id"]]
    assert client.get("/obligations", params={"external_ref": "nope"}, headers=sub).json() == []

    stranger = login(client, "submitter", org="Someone Else")
    assert (
        client.get("/obligations", params={"external_ref": "enr:1:1"}, headers=stranger).json()
        == []
    )


def test_a_duplicate_external_ref_is_a_conflict_the_host_can_resolve_by_lookup(client):
    sub = login(client, "submitter")
    first = _ob(client, sub, external_ref="enr:9:1")
    dup = client.post(
        "/obligations",
        headers=sub,
        json={
            "learner_ref": "learner-1",
            "type": "instalment",
            "amount_minor": 4000,
            "currency": "USD",
            "external_ref": "enr:9:1",
        },
    )
    assert dup.status_code == 409
    again = client.get("/obligations", params={"external_ref": "enr:9:1"}, headers=sub).json()
    assert [r["payment_code"] for r in again] == [first["payment_code"]]
