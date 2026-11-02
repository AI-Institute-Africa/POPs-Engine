"""Admins hear about every claim that needs a human, once, and again when it waits too long."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.entitlements import outbox
from app.models import Claim, OutboxEvent
from app.policy.engine import PolicyConfig
from app.workers import tasks
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import _obligation, _submit


def _events(session, event_type: str) -> list[OutboxEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == event_type)
            .order_by(OutboxEvent.created_at)
        )
    )


def _in_review(client) -> dict:
    """A claim the readers disagree on, so it waits for a human."""
    sub = login(client, "submitter")
    png = make_receipt(ecocash_fields(), ocr=ecocash_fields(amount="48.00"))
    claim = _submit(client, sub, _obligation(client, sub), png)
    assert claim["state"] == "PENDING_REVIEW"
    return claim


def _backdate(claim_id: str, minutes: int) -> None:
    with get_sessionmaker()() as s:
        s.execute(
            update(Claim)
            .where(Claim.claim_id == uuid.UUID(claim_id))
            .values(review_entered_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        s.commit()


def test_review_event_is_sent_once_when_a_claim_needs_a_human(client, session):
    claim = _in_review(client)
    (event,) = _events(session, "ops.review_pending")
    assert event.payload["claim_id"] == claim["claim_id"]
    assert event.payload["state"] == "PENDING_REVIEW"
    assert "EXTRACTION_UNCONFIRMED" in event.payload["reason_codes"]
    assert session.get(Claim, uuid.UUID(claim["claim_id"])).review_entered_at is not None


def test_a_claim_waiting_for_money_does_not_page_anyone(client, session):
    sub = login(client, "submitter")
    claim = _submit(client, sub, _obligation(client, sub), make_receipt(ecocash_fields()))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"
    assert _events(session, "ops.review_pending") == []


def test_review_event_reaches_the_admin_webhook_without_personal_data(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_alert_webhook_url", "https://hooks.slack.test/x")
    sub = login(client, "submitter")
    png = make_receipt(ecocash_fields(sender="Tendai Moyo"), ocr=ecocash_fields(amount="48.00"))
    _submit(client, sub, _obligation(client, sub), png)
    sent: list[httpx.Request] = []
    http = httpx.Client(
        transport=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200))
    )
    assert outbox.deliver_pending(session, client=http) >= 1
    body = sent[0].read().decode()
    assert "needs review" in body and "EXTRACTION_UNCONFIRMED" in body
    assert "Tendai" not in body and "learner-1" not in body


def test_leaving_review_stops_the_clock(client, session):
    claim = _in_review(client)
    rev = login(client, "reviewer")
    r = client.post(
        f"/review/claims/{claim['claim_id']}/request-info",
        headers=rev,
        json={"message": "please send a clearer copy"},
    )
    assert r.status_code == 200, r.text
    session.expire_all()
    row = session.get(Claim, uuid.UUID(claim["claim_id"]))
    assert row.state == "NEEDS_INFORMATION" and row.review_entered_at is None
    _backdate(claim["claim_id"], 0)  # even if a stale timestamp were left behind…
    with get_sessionmaker()() as s:
        s.execute(
            update(Claim).where(Claim.claim_id == row.claim_id).values(review_entered_at=None)
        )
        s.commit()
    assert tasks.run_review_aging() == 0  # …a claim no longer waiting is never nudged


def test_overdue_reminders_fire_once_per_sla_level(client, session):
    claim = _in_review(client)
    cid = claim["claim_id"]
    assert tasks.run_review_aging() == 0  # just arrived

    _backdate(cid, 90)  # past the 60-minute level
    assert tasks.run_review_aging() == 1
    assert tasks.run_review_aging() == 0  # same level: no repeat

    _backdate(cid, 300)  # past 240
    assert tasks.run_review_aging() == 1
    _backdate(cid, 60 * 30)  # past 1440
    assert tasks.run_review_aging() == 1
    _backdate(cid, 60 * 90)  # nothing beyond the last level
    assert tasks.run_review_aging() == 0

    events = _events(session, "ops.review_overdue")
    assert [e.payload["level"] for e in events] == [1, 2, 3]
    assert all(e.payload["claim_id"] == cid for e in events)
    assert "Tendai" not in str([e.payload for e in events])


def test_aging_jumps_to_the_highest_level_reached(client, session):
    claim = _in_review(client)
    _backdate(claim["claim_id"], 60 * 30)  # waited past every level while the worker was down
    assert tasks.run_review_aging() == 1  # one message, not three
    (event,) = _events(session, "ops.review_overdue")
    assert event.payload["level"] == 3


def test_review_sla_must_be_positive_and_ascending():
    assert PolicyConfig().review_sla_minutes == [60, 240, 1440]
    assert PolicyConfig(review_sla_minutes=[30]).review_sla_minutes == [30]
    for bad in ([0, 60], [60, 60], [240, 60], [-5]):
        with pytest.raises(ValidationError):
            PolicyConfig(review_sla_minutes=bad)


def test_email_wording_matches_the_event():
    def ev(kind: str, **payload) -> OutboxEvent:
        return OutboxEvent(event_type=kind, event_key=kind, payload=payload, org_id=uuid.uuid4())

    assert "Suspected fake" in outbox._email_wording(ev("fraud.suspected", score=70))[0]
    subject, hint = outbox._email_wording(ev("ops.review_pending"))
    assert "needs review" in subject and "Review queue" in hint
    assert "overdue" in outbox._email_wording(ev("ops.review_overdue"))[0]
    assert outbox._email_wording(ev("ops.alert_source_stale")) == ("[POPs] Operations alert", "")


def test_enqueue_event_is_idempotent(session):
    org = uuid.uuid4()
    from app.models import Organisation

    session.add(Organisation(org_id=org, name="Idem"))
    session.commit()
    assert outbox.enqueue_event(session, org, "ops.x", "k:1", {"text": "a"}) is True
    session.commit()
    assert outbox.enqueue_event(session, org, "ops.x", "k:1", {"text": "b"}) is False
    session.commit()
    assert len(_events(session, "ops.x")) == 1
