"""CSV export of allocations and reversals for finance and bookkeeping."""

import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.ledger import export
from app.models import AuditEvent
from tests.conftest import login
from tests.test_flow import TXN, _import, _stmt_row

LAST4 = TXN[-4:]


def _rows(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")  # Excel's byte-order mark
    return list(csv.DictReader(io.StringIO(text)))


def _allocate(client, learner="learner-1", amount=4000, **body):
    sub, rev, appr = (login(client, r) for r in ("submitter", "reviewer", "approver"))
    ob = client.post(
        "/obligations",
        headers=sub,
        json={
            "learner_ref": learner,
            "type": "instalment",
            "amount_minor": amount,
            "currency": "USD",
            "description": "x",
        },
    ).json()
    _import(client, rev, [_stmt_row()])
    cid = next(c["credit_id"] for c in client.get("/credits", headers=rev).json())
    r = client.post(
        f"/credits/{cid}/allocate",
        headers=appr,
        json={"obligation_id": ob["obligation_id"], "txn_last4": LAST4, **body},
    )
    assert r.status_code == 200, r.text
    return ob, r.json()["allocation"], appr, rev


def test_each_allocation_is_a_row_and_a_reversal_is_negative(client):
    ob, alloc, appr, rev = _allocate(client)
    client.post(
        f"/allocations/{alloc['allocation_id']}/reverse",
        headers=appr,
        json={"reason": "wrong learner"},
    )
    rows = _rows(client.get("/allocations/export", headers=rev))
    assert [r["kind"] for r in rows] == ["allocation", "reversal"]
    first, second = rows
    assert first["amount"] == "40.00" and first["amount_minor"] == "4000"
    assert second["amount"] == "-40.00" and second["amount_minor"] == "-4000"
    assert second["reverses"] == alloc["allocation_id"] and second["reason"] == "wrong learner"
    assert (
        first["obligation_id"] == ob["obligation_id"]
        and first["payment_code"] == ob["payment_code"]
    )
    assert first["provider_txn_id"] == TXN and first["credit_source"] == "statement"
    assert first["currency"] == "USD" and first["created_by"].startswith("user:")
    assert sum(int(r["amount_minor"]) for r in rows) == 0  # a column total is the net paid


def test_filters_by_date_and_kind(client):
    _, alloc, appr, rev = _allocate(client)
    client.post(
        f"/allocations/{alloc['allocation_id']}/reverse",
        headers=appr,
        json={"reason": "wrong learner"},
    )
    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)
    same_day = _rows(client.get(f"/allocations/export?since={today}&until={today}", headers=rev))
    assert len(same_day) == 2  # both bounds are inclusive
    assert _rows(client.get(f"/allocations/export?since={tomorrow}", headers=rev)) == []
    assert (
        _rows(client.get(f"/allocations/export?until={today - timedelta(days=1)}", headers=rev))
        == []
    )
    only = _rows(client.get("/allocations/export?kind=reversal", headers=rev))
    assert [r["kind"] for r in only] == ["reversal"]
    assert client.get("/allocations/export?kind=refund", headers=rev).status_code == 422


def test_an_empty_export_is_just_the_header(client):
    rev = login(client, "reviewer")
    r = client.get("/allocations/export", headers=rev)
    assert r.status_code == 200
    assert r.content.decode("utf-8-sig").strip() == ",".join(export.COLUMNS)


def test_text_that_would_run_as_a_formula_is_defused(client):
    _, _, _, rev = _allocate(client, learner='=HYPERLINK("http://evil.test","click")')
    (row,) = _rows(client.get("/allocations/export", headers=rev))
    assert row["learner_ref"].startswith("'=")  # shown as text, not executed
    assert row["amount"] == "40.00"  # real numbers are untouched


def test_safe_cell_leaves_numbers_and_empty_values_alone():
    assert export.safe_cell("-40.00") == "-40.00"
    assert export.safe_cell("=1+1") == "'=1+1"
    assert export.safe_cell("+27123") == "'+27123"
    assert export.safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert export.safe_cell(None) == "" and export.safe_cell(4000) == 4000
    assert export.safe_cell("learner-1") == "learner-1"


def test_only_staff_see_it_and_only_their_own_organisation(client):
    _, _, _, rev = _allocate(client)
    assert client.get("/allocations/export", headers=login(client, "submitter")).status_code == 403
    assert client.get("/allocations/export").status_code in (401, 403)
    stranger = login(client, "reviewer", org="Other")
    assert _rows(client.get("/allocations/export", headers=stranger)) == []


def test_every_export_is_audited(client, session):
    _, _, _, rev = _allocate(client)
    client.get("/allocations/export?kind=allocation", headers=rev)
    session.expire_all()
    (event,) = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "allocations.exported")
    ).all()
    assert event.after["rows"] == 1 and event.after["kind"] == "allocation"
    assert event.actor.startswith("user:")


def test_a_huge_export_asks_for_a_narrower_range(client, monkeypatch):
    _, alloc, appr, rev = _allocate(client)
    client.post(
        f"/allocations/{alloc['allocation_id']}/reverse",
        headers=appr,
        json={"reason": "wrong learner"},
    )
    monkeypatch.setattr(export, "MAX_ROWS", 1)
    r = client.get("/allocations/export", headers=rev)
    assert r.status_code == 413 and "narrow" in r.json()["detail"]
