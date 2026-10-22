"""Shadow mode (blueprint phase 4, innovation 8): proposals are advisory, never actions."""

from app.core.config import get_settings
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit


def _enable_shadow(monkeypatch):
    monkeypatch.setattr(get_settings(), "shadow_mode_enabled", True)


def _scorecard(client, headers, days=30):
    r = client.get(f"/shadow/scorecard?days={days}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _proposer(card, name):
    return next((p for p in card["proposers"] if p["proposer"] == name), None)


def test_shadow_disabled_by_default(client):
    """No env flag, no proposals: shadow mode is opt-in and changes nothing when off."""
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")
    assert client.get("/shadow/proposals", headers=rev).json() == []
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert detail["shadow_proposals"] == []
    card = _scorecard(client, rev)
    assert card["enabled"] is False and card["proposals"] == 0


def test_rules_proposal_recorded_and_claim_state_unchanged(client, monkeypatch):
    """The rules baseline proposes; the claim's state, tier and reasons are untouched."""
    _enable_shadow(monkeypatch)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")

    after = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert after["state"] == claim["state"]
    assert after["approval_tier"] == claim["approval_tier"]
    assert after["reason_codes"] == claim["reason_codes"]

    rows = client.get("/shadow/proposals", headers=rev).json()
    assert len(rows) == 1
    assert rows[0]["proposer"] == "rules-baseline"
    assert rows[0]["proposed_outcome"] in ("approve", "reject", "needs_info", "review")

    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert len(detail["shadow_proposals"]) == 1
    # a claimant never sees advisory AI output
    assert client.get("/shadow/proposals", headers=sub).status_code == 403


def test_proposal_is_idempotent(client, monkeypatch):
    """A retried dispatch does not create a second proposal for the same claim."""
    from app.core.db import session_scope
    from app.shadow.proposer import propose_for_claim

    _enable_shadow(monkeypatch)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")
    # the inline pipeline already proposed once; a retry (unique claim+proposer) is a no-op
    with session_scope() as session:
        assert propose_for_claim(session, claim["claim_id"]) == []
    assert len(client.get("/shadow/proposals", headers=rev).json()) == 1


def _fake_ai_post(outcome="approve", credit_id=None):
    """Stand-in for the Ollama chat call so the AI proposer is tested without a model."""
    import json as json_mod

    def post(url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors httpx.post
        content = {
            "outcome": outcome,
            "credit_id": credit_id,
            "risk_flags": ["amount_close_to_limit"],
            "confidence": 0.77,
            "rationale": "The statement line matches the receipt amount and date.",
        }

        class R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": json_mod.dumps(content)}}

        return R()

    return post


def test_ai_proposer_records_advisory_row_when_allowed(client, monkeypatch):
    """With the AI configured and allowed, a model proposal is recorded beside the baseline."""
    from app.shadow import proposer as shadow

    _enable_shadow(monkeypatch)
    monkeypatch.setattr(get_settings(), "vlm_backend", "ollama")
    monkeypatch.setattr(shadow.httpx, "post", _fake_ai_post())
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")

    rows = client.get("/shadow/proposals", headers=rev).json()
    names = {r["proposer"] for r in rows}
    assert names == {"rules-baseline", "ollama:test-model"} or "ollama:" in " ".join(names)
    ai = next(r for r in rows if r["proposer"].startswith("ollama:"))
    assert ai["confidence"] == 0.77
    assert ai["rationale"].startswith("The statement line")
    # still advisory: nothing about the claim changed
    claims = client.get("/claims", headers=rev).json()
    assert all(c["state"] != "APPROVED" for c in claims)


def test_kill_switch_stops_the_ai_proposer_but_not_the_baseline(client, monkeypatch):
    """The AI kill switch that turns off the reader also turns off the AI proposer."""
    from app.shadow import proposer as shadow

    _enable_shadow(monkeypatch)
    monkeypatch.setattr(get_settings(), "vlm_backend", "ollama")
    monkeypatch.setattr(shadow.httpx, "post", _fake_ai_post())
    sub, adm, rev = (
        login(client, "submitter"),
        login(client, "admin"),
        login(client, "reviewer"),
    )
    r = client.post(
        "/admin/guardrails",
        headers=adm,
        json={
            "name": "AI kill switch",
            "kind": "ai_kill_switch",
            "action": "disable_ai",
            "config": {},
        },
    )
    assert r.status_code == 201, r.text

    ob = _obligation(client, sub)
    _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")
    rows = client.get("/shadow/proposals", headers=rev).json()
    assert [r["proposer"] for r in rows] == ["rules-baseline"]  # no model proposal


def test_ai_proposer_fails_soft(client, monkeypatch):
    """A model outage costs a proposal, never a claim (fail-soft by design)."""
    import httpx

    from app.shadow import proposer as shadow

    _enable_shadow(monkeypatch)
    monkeypatch.setattr(get_settings(), "vlm_backend", "ollama")

    def boom(*a, **kw):
        raise httpx.ConnectError("model unreachable")

    monkeypatch.setattr(shadow.httpx, "post", boom)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()), declared_amount="40.00")
    # the claim flowed and was decided (fail closed, not failed): only the advisory row is
    # missing, and the deterministic baseline is still there
    assert claim["state"] != "APPROVED"
    rows = client.get("/shadow/proposals", headers=rev).json()
    assert [r["proposer"] for r in rows] == ["rules-baseline"]


def test_scorecard_counts_agreement_and_false_approval(client, monkeypatch):
    """A proposed approve the reviewer rejects is a false approval - the number that gates
    automation (blueprint innovation 8)."""
    _enable_shadow(monkeypatch)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    # both credits exist before the claims, so the pipeline matches and the baseline proposes
    # "approve" for each
    _import(
        client,
        rev,
        [_stmt_row(txn=TXN), _stmt_row(txn="PP251021.1432.A12346")],
    )
    c1 = _submit(client, sub, ob, make_receipt(ecocash_fields(), seed=1), declared_amount="40.00")
    c2 = _submit(
        client,
        sub,
        ob,
        make_receipt(ecocash_fields(txn="PP251021.1432.A12346"), seed=2),
        declared_amount="40.00",
    )
    assert c1["state"] == "FINANCIALLY_CONFIRMED" and c2["state"] == "FINANCIALLY_CONFIRMED"

    # the human approves one and rejects the other (say: the payer's money was reversed)
    detail = client.get(f"/review/claims/{c2['claim_id']}", headers=rev).json()
    ok = client.post(
        f"/review/claims/{c2['claim_id']}/confirm",
        headers=rev,
        json={
            "credit_id": detail["candidates"][0]["credit"]["credit_id"],
            "txn_last4": detail["candidates"][0]["credit"]["provider_txn_id"][-4:],
        },
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        f"/review/claims/{c1['claim_id']}/reject",
        headers=rev,
        json={"reason": "PAYMENT_FAILED_OR_REVERSED", "note": "reversed same day"},
    )
    assert bad.status_code == 200, bad.text

    card = _scorecard(client, rev)
    assert card["enabled"] is True
    assert card["proposals"] == 2
    assert card["scored"] == 2
    rules = _proposer(card, "rules-baseline")
    assert rules["agreements"] == 1  # c2: proposed approve, human approved
    assert rules["false_approvals"] == 1  # c1: proposed approve, human rejected
    assert rules["false_holds"] == 0
    assert rules["false_approval_rate"] == 0.5
    assert 0.0 <= rules["agreement_rate"] <= 1.0


def test_scorecard_counts_false_hold(client, monkeypatch):
    """A proposed reject the reviewer approves is a false hold: the human override the AI
    would have blocked."""
    _enable_shadow(monkeypatch)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    receipt = make_receipt(ecocash_fields(), seed=31)
    c1 = _submit(client, sub, ob, receipt, declared_amount="40.00")
    # the same file again: duplicate -> the baseline proposes "reject"
    c2 = _submit(client, sub, ob, receipt, declared_amount="40.00")
    assert c2["state"] == "DUPLICATE_HOLD"

    # the reviewer investigates and confirms the duplicate claim against the real credit
    _import(client, rev, [_stmt_row()])
    # the reused receipt also opened a suspected-fake alert, which locks confirmation
    # until an admin clears it (merged fraud feature): clear it, then confirm
    adm = login(client, "admin")
    alert = next(
        a
        for a in client.get("/fraud/alerts?status=open", headers=adm).json()
        if a["claim_id"] == c2["claim_id"]
    )
    cleared = client.post(
        f"/fraud/alerts/{alert['alert_id']}/resolve",
        headers=adm,
        json={"resolution": "false_positive", "note": "reuse explained by first claim"},
    )
    assert cleared.status_code == 200, cleared.text

    detail = client.get(f"/review/claims/{c2['claim_id']}", headers=rev).json()
    credit = detail["candidates"][0]["credit"]
    ok = client.post(
        f"/review/claims/{c2['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": credit["credit_id"], "txn_last4": credit["provider_txn_id"][-4:]},
    )
    assert ok.status_code == 200, ok.text

    card = _scorecard(client, rev)
    rules = _proposer(card, "rules-baseline")
    assert rules is not None
    # c2 was proposed "reject" but approved by the human: a false hold, not an approval
    assert rules["false_holds"] >= 1
    assert card["scored"] >= 1
    assert c1["claim_id"] != c2["claim_id"]
