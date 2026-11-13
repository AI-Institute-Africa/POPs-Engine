"""Bulk allocation planning and request ids: pure, no database."""

import uuid
from types import SimpleNamespace

from app.credits.bulk import RowIn, plan_rows, row_request_id


def _obl(code: str, currency="USD"):
    return SimpleNamespace(obligation_id=uuid.uuid4(), payment_code=code, currency=currency)


def _plan(rows, *, available=10_000, owed=None, currency="USD", waiting=()):
    obls = {"CG-A": _obl("CG-A"), "CG-B": _obl("CG-B"), "CG-Z": _obl("CG-Z", "ZWG")}
    by_id = {o.obligation_id: o for o in obls.values()}
    outstanding = {
        obls[c].obligation_id: n
        for c, n in (owed or {"CG-A": 6000, "CG-B": 6000, "CG-Z": 5000}).items()
    }
    plans = plan_rows(
        rows,
        credit_available=available,
        credit_currency=currency,
        by_code=obls,
        by_id=by_id,
        outstanding=outstanding,
        waiting={obls[c].obligation_id for c in waiting},
    )
    return plans, obls


def test_ready_rows_use_running_balances():
    plans, _ = _plan([RowIn("CG-A", amount_minor=6000), RowIn("CG-B", amount_minor=4000)])
    assert [p.status for p in plans] == ["ready", "ready"]


def test_credit_runs_out_part_way():
    plans, _ = _plan([RowIn("CG-A", amount_minor=6000), RowIn("CG-B", amount_minor=6000)])
    assert plans[0].status == "ready"
    assert plans[1].status == "fix" and "at most 4000" in plans[1].reason


def test_missing_amount_is_a_check_and_takes_the_most_allowed():
    plans, _ = _plan([RowIn("CG-A")])
    assert plans[0].status == "check" and plans[0].amount_minor == 6000


def test_unknown_code_currency_mismatch_and_waiting_claim_need_fixing():
    plans, _ = _plan(
        [
            RowIn("NOPE", amount_minor=1),
            RowIn("CG-Z", amount_minor=1),
            RowIn("CG-A", amount_minor=1),
        ],
        waiting=["CG-A"],
    )
    assert [p.status for p in plans] == ["fix", "fix", "fix"]


def test_codes_are_case_and_space_insensitive():
    plans, _ = _plan([RowIn(" cg-a ", amount_minor=100)])
    assert plans[0].status == "ready"


def test_same_invoice_twice_cannot_overpay():
    plans, _ = _plan([RowIn("CG-A", amount_minor=5000), RowIn("CG-A", amount_minor=5000)])
    assert plans[0].status == "ready" and plans[1].status == "fix"


def test_zero_or_exhausted_credit_is_fixed():
    plans, _ = _plan([RowIn("CG-A", amount_minor=1)], available=0)
    assert plans[0].status == "fix" and "used up" in plans[0].reason


def test_request_ids_are_stable_and_distinct():
    run, ob = uuid.uuid4(), uuid.uuid4()
    assert row_request_id(run, 0, ob, 100) == row_request_id(run, 0, ob, 100)
    assert row_request_id(run, 0, ob, 100) != row_request_id(run, 1, ob, 100)
    assert row_request_id(run, 0, ob, 100) != row_request_id(uuid.uuid4(), 0, ob, 100)
