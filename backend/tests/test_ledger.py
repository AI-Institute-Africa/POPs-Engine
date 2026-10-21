"""Ledger invariants (error layer 7): no over-allocation, no edits, idempotent, race-safe."""

import threading
import uuid
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_engine, get_sessionmaker
from app.ledger import service as ledger
from app.models import Allocation, Credit, Obligation, Organisation


def _setup(session, credit_minor=4000, obligation_minor=4000, currency="USD", status="success"):
    org = Organisation(name=f"org-{uuid.uuid4().hex[:6]}")
    session.add(org)
    session.flush()
    ref = uuid.uuid4().hex[:10].upper()
    credit = Credit(
        org_id=org.org_id,
        source="statement",
        provider="BANK",
        provider_txn_id=ref,
        reference_norm=ref,
        amount_minor=credit_minor,
        currency=currency,
        occurred_at=datetime.now(UTC),
        status=status,
        beneficiary="MAIN",
        created_by="test",
    )
    ob = Obligation(
        org_id=org.org_id,
        learner_ref="L1",
        type="instalment",
        amount_minor=obligation_minor,
        currency="USD",
        payment_code=f"CG-{uuid.uuid4().hex[:7].upper()}",
    )
    session.add_all([credit, ob])
    session.commit()
    return org, credit, ob


def test_allocate_min_of_available_outstanding_authorised(session):
    _, credit, ob = _setup(session, credit_minor=5000, obligation_minor=4000)
    a = ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=5000,
        idempotency_key="k1",
        actor="t",
    )
    session.commit()
    assert a.amount_minor == 4000  # overpayment: remainder stays unapplied
    assert ledger.credit_available(session, credit) == 1000
    assert ledger.balance(session, ob).status == "paid"


def test_idempotent(session):
    _, credit, ob = _setup(session)
    a1 = ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=4000,
        idempotency_key="same",
        actor="t",
    )
    session.commit()
    a2 = ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=4000,
        idempotency_key="same",
        actor="t",
    )
    assert a1.allocation_id == a2.allocation_id


def test_partial_payment(session):
    _, credit, ob = _setup(session, credit_minor=2500, obligation_minor=4000)
    ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=2500,
        idempotency_key="p",
        actor="t",
    )
    session.commit()
    bal = ledger.balance(session, ob)
    assert (bal.paid_minor, bal.outstanding_minor, bal.status) == (2500, 1500, "partially_paid")


def test_service_refuses_unsettled_and_wrong_currency(session):
    _, credit, ob = _setup(session, status="pending")
    with pytest.raises(ledger.LedgerError) as e:
        ledger.allocate(
            session,
            credit_id=credit.credit_id,
            obligation_id=ob.obligation_id,
            authorised_minor=4000,
            idempotency_key="x",
            actor="t",
        )
    assert e.value.code == "CREDIT_NOT_SUCCESS"
    session.rollback()
    _, credit, ob = _setup(session, currency="ZWG")
    with pytest.raises(ledger.LedgerError) as e:
        ledger.allocate(
            session,
            credit_id=credit.credit_id,
            obligation_id=ob.obligation_id,
            authorised_minor=4000,
            idempotency_key="y",
            actor="t",
        )
    assert e.value.code == "CURRENCY_MISMATCH"


def test_trigger_blocks_over_allocation_even_bypassing_service(session):
    org, credit, ob = _setup(session)
    session.add(
        Allocation(
            org_id=org.org_id,
            credit_id=credit.credit_id,
            obligation_id=ob.obligation_id,
            amount_minor=4001,
            currency="USD",
            idempotency_key="raw",
            created_by="t",
        )
    )
    with pytest.raises(DBAPIError, match="over-allocation"):
        session.flush()


def test_append_only(session):
    _, credit, ob = _setup(session)
    a = ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=4000,
        idempotency_key="ao",
        actor="t",
    )
    session.commit()
    for stmt in ("UPDATE allocations SET amount_minor = 1", "DELETE FROM allocations"):
        with get_engine().connect() as conn, pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(stmt))
    assert session.get(Allocation, a.allocation_id).amount_minor == 4000


def test_reversal_restores_balance_and_is_single(session):
    _, credit, ob = _setup(session)
    a = ledger.allocate(
        session,
        credit_id=credit.credit_id,
        obligation_id=ob.obligation_id,
        authorised_minor=4000,
        idempotency_key="r",
        actor="t",
    )
    session.commit()
    r1 = ledger.reverse(session, allocation_id=a.allocation_id, actor="t", reason="chargeback")
    session.commit()
    r2 = ledger.reverse(session, allocation_id=a.allocation_id, actor="t", reason="again")
    assert r1.allocation_id == r2.allocation_id
    assert ledger.balance(session, ob).paid_minor == 0
    assert ledger.credit_available(session, credit) == 4000


def test_concurrent_allocations_never_overspend(session):
    """Ten workers race to spend one $40 credit across ten $40 invoices: exactly one wins."""
    org, credit, _ = _setup(session)
    obligations = []
    for i in range(10):
        ob = Obligation(
            org_id=org.org_id,
            learner_ref=f"L{i}",
            type="instalment",
            amount_minor=4000,
            currency="USD",
            payment_code=f"CG-R{i}{uuid.uuid4().hex[:5]}",
        )
        session.add(ob)
        obligations.append(ob)
    session.commit()
    barrier = threading.Barrier(10)
    results: list[str] = []

    def worker(ob_id):
        s = get_sessionmaker()()
        try:
            barrier.wait()
            ledger.allocate(
                s,
                credit_id=credit.credit_id,
                obligation_id=ob_id,
                authorised_minor=4000,
                idempotency_key=f"race-{ob_id}",
                actor="t",
            )
            s.commit()
            results.append("ok")
        except ledger.LedgerError as e:
            s.rollback()
            results.append(e.code)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(o.obligation_id,)) for o in obligations]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert ledger.credit_allocated(session, credit.credit_id) == 4000


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    ops=st.lists(
        st.tuples(st.sampled_from(["alloc", "reverse"]), st.integers(1, 3000), st.integers(0, 2)),
        min_size=1,
        max_size=15,
    )
)
def test_random_operation_sequences_hold_invariants(session, ops):
    org, credit, _ = _setup(session, credit_minor=5000)
    obs = []
    for i in range(3):
        ob = Obligation(
            org_id=org.org_id,
            learner_ref=f"H{i}",
            type="instalment",
            amount_minor=2000,
            currency="USD",
            payment_code=f"CG-H{uuid.uuid4().hex[:6]}",
        )
        session.add(ob)
        obs.append(ob)
    session.commit()
    allocations: list[uuid.UUID] = []
    for n, (op, amount, idx) in enumerate(ops):
        try:
            if op == "alloc":
                a = ledger.allocate(
                    session,
                    credit_id=credit.credit_id,
                    obligation_id=obs[idx].obligation_id,
                    authorised_minor=amount,
                    idempotency_key=f"h-{credit.credit_id}-{n}",
                    actor="t",
                )
                allocations.append(a.allocation_id)
            elif allocations:
                ledger.reverse(
                    session,
                    allocation_id=allocations[idx % len(allocations)],
                    actor="t",
                    reason="h",
                )
            session.commit()
        except ledger.LedgerError:
            session.rollback()
        assert 0 <= ledger.credit_allocated(session, credit.credit_id) <= credit.amount_minor
        for ob in obs:
            assert 0 <= ledger.obligation_paid(session, ob.obligation_id) <= ob.amount_minor
