"""Near-miss reference hints: shown to reviewers, never able to open the reference gate."""

import pytest

from app.reconcile.near import edit_distance, near_reference
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import _import, _obligation, _stmt_row, _submit

RECORDED = "PP251021.1432.A12340"  # the bank's reference, ending in a zero
READ_AS = "PP251021.1432.A1234O"  # the receipt was read with a letter O


def test_a_lookalike_character_is_labelled():
    hint = near_reference("PP2510211432A1234O", "PP2510211432A12340")
    assert hint == {"kind": "lookalike_characters", "distance": 1}
    assert near_reference("ABCD1234IL", "ABCD12341L")["kind"] == "lookalike_characters"


def test_a_small_typo_is_labelled():
    assert near_reference("PP2510211432A12341", "PP2510211432A12340") == {
        "kind": "typo",
        "distance": 1,
    }
    assert near_reference("PP2510211432A12399", "PP2510211432A12340")["distance"] == 2


@pytest.mark.parametrize(
    "claimed, recorded",
    [
        ("PP2510211432A12345", "PP2510211432A12345"),  # identical: nothing to hint
        ("PP2510211432A12345", "PP2510211432A19999"),  # three characters off
        ("SHORT1", "SHORT2"),  # too short: collides by chance
        ("PP2510211432A12345", "PP2510211432"),  # very different length
        (None, "PP2510211432A12345"),
        ("PP2510211432A12345", None),
        ("", ""),
    ],
)
def test_no_hint_when_it_would_mislead(claimed, recorded):
    assert near_reference(claimed, recorded) is None


def test_edit_distance_gives_up_early():
    assert edit_distance("kitten", "sitting", cutoff=5) == 3
    assert edit_distance("abcdef", "uvwxyz", cutoff=2) == 3  # gave up: more than the cutoff
    assert edit_distance("abc", "abcdefghij", cutoff=2) == 3


def test_a_near_miss_shows_in_review_but_cannot_be_confirmed(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row(txn=RECORDED)])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(txn=READ_AS)))
    assert claim["state"] == "AWAITING_FINANCIAL_CONFIRMATION"  # the exact gate did not open

    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    (cand,) = detail["candidates"]
    assert cand["passed"] is False
    assert any(g["name"] == "reference" and not g["passed"] for g in cand["gates"])
    assert cand["near_miss"]["kind"] == "lookalike_characters"

    # advisory only: a reviewer still cannot confirm against it
    r = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": cand["credit"]["credit_id"], "txn_last4": RECORDED[-4:]},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "GATES_FAILED"


def test_correcting_the_misread_reference_is_the_way_through(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row(txn=RECORDED)])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(txn=READ_AS)))

    fixed = client.post(
        f"/review/claims/{claim['claim_id']}/fields",
        headers=rev,
        json={
            "name": "transaction_id",
            "value": RECORDED,
            "reason": "the receipt shows a zero, not the letter O",
        },
    )
    assert fixed.status_code == 200, fixed.text
    after = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert after["claim"]["state"] == "FINANCIALLY_CONFIRMED"
    assert after["candidates"][0]["passed"] is True and after["candidates"][0]["near_miss"] is None
    done = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": after["candidates"][0]["credit"]["credit_id"], "txn_last4": "2340"},
    )
    assert done.status_code == 200 and done.json()["claim"]["state"] == "APPROVED"


def test_a_different_payment_is_not_called_a_near_miss(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row(txn="FT26294XYZ98765")])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert all(c["near_miss"] is None for c in detail["candidates"])
