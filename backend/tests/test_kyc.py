"""KYC end to end: intake -> pipeline -> rules or a reviewer -> status, and what it gates."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.intake.storage import get_storage
from app.kyc import auto, service
from app.models import KycDecision, KycEvidence, KycState, KycVerification
from tests.conftest import login
from tests.devtools_ids import GOOD_FIELDS, front, selfie  # noqa: F401  (re-exported helpers)
from tests.test_auto_approval import _policy as _pay_policy
from tests.test_auto_approval import _state_after_statement_first

KYC_ON = {"kyc_auto_verify_mode": "on", "kyc_allowed_countries": ["ZWE"]}


def _policy(client, **cfg) -> None:
    r = client.post(
        "/admin/policy",
        headers=login(client, "admin"),
        json={"version": f"kyc-{uuid.uuid4().hex[:8]}", "config": {**KYC_ON, **cfg}},
    )
    assert r.status_code == 201, r.text


def _submit(
    client,
    h,
    card=None,
    face=None,
    *,
    subject_type="payer",
    subject_ref="learner-1",
    back=None,
    expect=201,
):
    files = {
        "front": ("id.png", card or front(), "image/png"),
        "selfie": ("me.png", face or selfie(), "image/png"),
    }
    if back:
        files["back"] = ("back.png", back, "image/png")
    data = {"subject_type": subject_type}
    if subject_ref is not None:
        data["subject_ref"] = subject_ref
    r = client.post("/kyc/verifications", headers=h, data=data, files=files)
    assert r.status_code == expect, r.text
    return r.json()


def _detail(client, rev, kyc_id) -> dict:
    r = client.get(f"/kyc/verifications/{kyc_id}", headers=rev)
    assert r.status_code == 200, r.text
    return r.json()


# --- the clear case and every way it stops ----------------------------------------------------


def test_off_by_default_everything_waits_for_a_person(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    out = _submit(client, sub)
    d = _detail(client, rev, out["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and "DISABLED" in d["reason_codes"]
    assert d["scores"]["face"] > 0.8 and d["fields"]["values"]["document_number"] == "63123456K42"
    assert (
        client.get("/kyc/subjects/payer/learner-1/status", headers=sub).json()["verified"] is False
    )


def test_a_clear_case_is_verified_with_no_person(client, session):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    out = _submit(client, sub)
    assert out["state"] == "VERIFIED"
    d = _detail(client, rev, out["kyc_id"])
    assert d["reason_codes"] == ["AUTO_VERIFIED"] and d["doc_type"] == "national_id"
    assert d["scores"]["ocr_agreement"] == 1.0
    status = client.get("/kyc/subjects/payer/learner-1/status", headers=sub).json()
    assert status["verified"] and status["expires_at"]
    session.expire_all()
    dec = session.scalars(select(KycDecision).where(KycDecision.outcome == "VERIFIED")).one()
    assert dec.actor_id == "rule:kyc-auto" and dec.actor_type == "rule"
    assert dec.snapshot["auto"] and dec.snapshot["inputs"]["face_score"] > 0.8
    # the submitter sees a status, not the extracted personal details
    assert "fields" not in client.get(f"/kyc/verifications/{out['kyc_id']}", headers=sub).json()


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"selfie": lambda: selfie(2)}, "FACE_SCORE_LOW"),
        ({"fields": {"expiry_date": "01/01/2020"}}, "DOC_EXPIRED"),
        ({"fields": {"issuing_country": "KEN"}}, "COUNTRY_NOT_ALLOWED"),
        ({"ocr": {"document_number": "99-999999 Z 99"}}, "LOW_OCR_AGREEMENT"),
        ({"ocr": {"full_name": "PETER NCUBE"}}, "FIELD_CONFLICT"),
        (
            {"fields": {"document_number": "63-12"}, "ocr": {"document_number": "63-12"}},
            "NUMBER_FORMAT_INVALID",
        ),
        ({"selfie": lambda: selfie(1, variation=0.0)}, "SELFIE_COPIED_FROM_ID_PHOTO"),
    ],
)
def test_each_failure_goes_to_a_reviewer_never_to_approval_or_rejection(client, kwargs, code):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    card = front(fields=kwargs.get("fields"), ocr=kwargs.get("ocr"))
    face = kwargs["selfie"]() if "selfie" in kwargs else None
    out = _submit(client, sub, card, face)
    d = _detail(client, rev, out["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and code in d["reason_codes"], d["reason_codes"]


def test_a_reader_outage_fails_closed(client, monkeypatch):
    _policy(client)
    from app.kyc import readers

    monkeypatch.setattr(readers, "build_id_readers", lambda: [])
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    d = _detail(client, rev, _submit(client, sub)["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and "READERS_UNAVAILABLE" in d["reason_codes"]


def test_no_face_model_fails_closed(client, monkeypatch):
    _policy(client)
    monkeypatch.setattr(get_settings(), "kyc_face_backend", "none")
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    d = _detail(client, rev, _submit(client, sub)["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and "FACE_UNAVAILABLE" in d["reason_codes"]


def test_kill_switch_overrides_the_policy(client, monkeypatch):
    _policy(client)
    monkeypatch.setattr(get_settings(), "kyc_auto_disabled", True)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    d = _detail(client, rev, _submit(client, sub)["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and "KILL_SWITCH" in d["reason_codes"]


def test_shadow_mode_records_what_would_happen_and_changes_nothing(client, session):
    _policy(client, kyc_auto_verify_mode="shadow")
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    out = _submit(client, sub)
    assert _detail(client, rev, out["kyc_id"])["state"] == "NEEDS_REVIEW"
    session.expire_all()
    outcomes = {d.outcome for d in session.scalars(select(KycDecision))}
    assert "SHADOW_WOULD_VERIFY" in outcomes and "VERIFIED" not in outcomes


def test_unusable_captures_go_back_to_the_subject(client):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    out = _submit(client, sub, front(blank_face=True))
    assert out["state"] == "NEEDS_INFORMATION" and "photo" in out["message"].lower()
    assert "NO_FACE_ON_ID" in _detail(client, rev, out["kyc_id"])["reason_codes"]
    out = _submit(client, sub, face=selfie(blank=True))
    assert out["state"] == "NEEDS_INFORMATION"
    assert "NO_FACE_IN_SELFIE" in _detail(client, rev, out["kyc_id"])["reason_codes"]


# --- integrity across subjects ----------------------------------------------------------------


def test_the_same_id_number_on_two_people_is_flagged(client):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    first = _submit(client, sub, front(face_seed=1), selfie(1), subject_ref="learner-1")
    assert first["state"] == "VERIFIED"
    second = _submit(client, sub, front(face_seed=2), selfie(2), subject_ref="learner-2")
    d = _detail(client, rev, second["kyc_id"])
    assert d["state"] == "NEEDS_REVIEW" and "ID_NUMBER_USED_BY_OTHER_SUBJECT" in d["reason_codes"]


def test_the_same_person_may_resubmit_without_a_flag(client):
    _policy(client)
    sub = login(client, "submitter")
    assert _submit(client, sub)["state"] == "VERIFIED"
    assert _submit(client, sub)["state"] == "VERIFIED"


def test_a_reused_image_across_subjects_is_flagged(client):
    _policy(client)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    shared = selfie(1)
    _submit(client, sub, front(face_seed=1), shared, subject_ref="learner-1")
    d = _detail(
        client,
        rev,
        _submit(
            client,
            sub,
            front(face_seed=1, number="63-999999 K 42"),
            shared,
            subject_ref="learner-2",
        )["kyc_id"],
    )
    assert "IMAGE_REUSED_BY_OTHER_SUBJECT" in d["reason_codes"]


# --- reviewers --------------------------------------------------------------------------------


def test_reviewer_verifies_from_the_queue(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    out = _submit(client, sub)
    queue = client.get("/kyc/review/queue", headers=rev).json()
    assert [q["kyc_id"] for q in queue] == [out["kyc_id"]]
    r = client.post(
        f"/kyc/review/{out['kyc_id']}/decision",
        headers=rev,
        json={"outcome": "VERIFIED", "note": "checked against the original"},
    )
    assert r.status_code == 200 and r.json()["state"] == "VERIFIED"
    assert client.get("/kyc/subjects/payer/learner-1/status", headers=sub).json()["verified"]
    assert client.get("/kyc/review/queue", headers=rev).json() == []


def test_reject_and_request_info_need_a_reason_and_submitters_cannot_decide(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    kid = _submit(client, sub)["kyc_id"]
    assert (
        client.post(
            f"/kyc/review/{kid}/decision", headers=sub, json={"outcome": "VERIFIED"}
        ).status_code
        == 403
    )
    r = client.post(f"/kyc/review/{kid}/decision", headers=rev, json={"outcome": "REJECTED"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "NOTE_REQUIRED"
    r = client.post(
        f"/kyc/review/{kid}/decision",
        headers=rev,
        json={"outcome": "REJECTED", "note": "photo does not match"},
    )
    assert r.json()["state"] == "REJECTED"
    # decided is decided
    r = client.post(
        f"/kyc/review/{kid}/decision",
        headers=rev,
        json={"outcome": "VERIFIED", "note": "changed my mind"},
    )
    assert r.status_code == 409


def test_a_reviewer_cannot_review_what_they_submitted(client):
    rev = login(client, "reviewer")
    kid = _submit(client, rev)["kyc_id"]
    r = client.post(
        f"/kyc/review/{kid}/decision", headers=rev, json={"outcome": "VERIFIED", "note": "self"}
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "SELF_REVIEW"


def test_staff_checks_need_an_approver_and_staff_verify_themselves(client):
    appr = login(client, "approver", "boss@example.com")
    rev = login(client, "reviewer")
    out = _submit(client, appr, subject_type="staff", subject_ref=None)
    assert out["subject_ref"] and out["state"] == "NEEDS_REVIEW"
    r = client.post(
        f"/kyc/review/{out['kyc_id']}/decision",
        headers=rev,
        json={"outcome": "VERIFIED", "note": "ok"},
    )
    assert r.status_code == 403  # a reviewer may not clear staff
    other = login(client, "approver", "other@example.com")
    r = client.post(
        f"/kyc/review/{out['kyc_id']}/decision",
        headers=other,
        json={"outcome": "VERIFIED", "note": "ok"},
    )
    assert r.status_code == 200
    # only an admin verifies an organisation, and nobody verifies someone else's staff record
    assert (
        client.post(
            "/kyc/verifications",
            headers=login(client, "submitter"),
            data={"subject_type": "org"},
            files={
                "front": ("a.png", front(), "image/png"),
                "selfie": ("b.png", selfie(), "image/png"),
            },
        ).status_code
        == 403
    )


def test_evidence_images_are_for_reviewers_and_every_view_is_audited(client, session):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    kid = _submit(client, sub)["kyc_id"]
    assert client.get(f"/kyc/verifications/{kid}/evidence/front", headers=sub).status_code == 403
    r = client.get(f"/kyc/verifications/{kid}/evidence/front", headers=rev)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    session.expire_all()
    from app.models import AuditEvent

    assert session.scalars(
        select(AuditEvent).where(AuditEvent.action == "kyc.evidence_viewed")
    ).first()


# --- sampled audit of automatic verifications --------------------------------------------------


def test_auto_verifications_are_listed_and_audited_once(client):
    _policy(client, kyc_audit_pct=100)
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    kid = _submit(client, sub)["kyc_id"]
    listed = client.get("/kyc/review/auto-verified", headers=rev).json()
    assert listed["enabled"] and listed["summary"]["verified"] == 1
    assert listed["verifications"][0]["sampled"] is True
    r = client.post(
        f"/kyc/review/{kid}/audit-verdict",
        headers=rev,
        json={"verdict": "overturned", "note": "wrong person"},
    )
    assert r.status_code == 200
    again = client.post(f"/kyc/review/{kid}/audit-verdict", headers=rev, json={"verdict": "ok"})
    assert again.status_code == 409
    summary = client.get("/kyc/review/auto-verified", headers=rev).json()["summary"]
    assert (
        summary["audited"] == 1 and summary["overturned"] == 1 and summary["overturn_rate"] == 1.0
    )
    # an overturned verdict does not revoke by itself; revoking is a separate, audited decision
    r = client.post(
        f"/kyc/review/{kid}/decision",
        headers=rev,
        json={"outcome": "REJECTED", "note": "revoked after audit"},
    )
    assert r.json()["state"] == "REJECTED"
    assert not client.get("/kyc/subjects/payer/learner-1/status", headers=sub).json()["verified"]


def test_verdict_only_on_automatic_verifications(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    kid = _submit(client, sub)["kyc_id"]  # left for a person: policy is off
    r = client.post(f"/kyc/review/{kid}/audit-verdict", headers=rev, json={"verdict": "ok"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_AUTO_VERIFIED"


def test_sampling_is_deterministic():
    k = uuid.uuid4()
    assert auto.is_sampled(k, 100) and not auto.is_sampled(k, 0)
    assert auto.is_sampled(k, 37) == auto.is_sampled(k, 37)


# --- lifecycle --------------------------------------------------------------------------------


def test_verification_expires(client, session):
    _policy(client)
    sub = login(client, "submitter")
    kid = _submit(client, sub)["kyc_id"]
    session.execute(
        update(KycVerification).values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    session.commit()
    assert not client.get("/kyc/subjects/payer/learner-1/status", headers=sub).json()["verified"]
    assert service.expire_due(session) == 1
    session.commit()
    assert session.get(KycVerification, uuid.UUID(kid)).state == KycState.EXPIRED


def test_images_are_deleted_after_retention_but_the_record_stays(client, session):
    _policy(client, kyc_retention_days=30)
    sub = login(client, "submitter")
    kid = uuid.UUID(_submit(client, sub)["kyc_id"])
    keys = [e.object_key for e in session.scalars(select(KycEvidence))]
    assert len(keys) == 2 and all(get_storage().get(k) for k in keys)
    assert service.purge_evidence(session) == 0  # too soon
    session.execute(update(KycEvidence).values(received_at=datetime.now(UTC) - timedelta(days=31)))
    session.commit()
    assert service.purge_evidence(session) == 2
    session.commit()
    for k in keys:
        with pytest.raises(FileNotFoundError):
            get_storage().get(k)
    rows = session.scalars(select(KycEvidence)).all()
    assert all(e.purged_at for e in rows)
    assert session.get(KycVerification, kid).state == KycState.VERIFIED


def test_attempts_per_day_are_limited(client):
    _policy(client, kyc_max_attempts_per_day=1)
    sub = login(client, "submitter")
    _submit(client, sub)
    r = _submit(client, sub, expect=429)
    assert r["detail"]["code"] == "RATE_LIMITED"


def test_kyc_decisions_are_append_only(client, session):
    _submit(client, login(client, "submitter"))
    session.expire_all()
    with pytest.raises(DBAPIError):
        session.execute(text("UPDATE kyc_decisions SET outcome = 'VERIFIED'"))
    session.rollback()
    with pytest.raises(DBAPIError):
        session.execute(text("DELETE FROM kyc_decisions"))


def test_bad_uploads_are_refused(client):
    sub = login(client, "submitter")
    pdf = b"%PDF-1.4\n%%EOF"
    r = client.post(
        "/kyc/verifications",
        headers=sub,
        data={"subject_type": "payer", "subject_ref": "x"},
        files={
            "front": ("a.pdf", pdf, "application/pdf"),
            "selfie": ("b.png", selfie(), "image/png"),
        },
    )
    assert r.status_code == 422
    r = client.post(
        "/kyc/verifications",
        headers=sub,
        data={"subject_type": "payer"},
        files={
            "front": ("a.png", front(), "image/png"),
            "selfie": ("b.png", selfie(), "image/png"),
        },
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "BAD_SUBJECT"
    assert client.post("/kyc/verifications", data={"subject_type": "payer"}).status_code in (
        401,
        403,
    )


def test_other_orgs_cannot_see_a_verification(client):
    kid = _submit(client, login(client, "submitter"))["kyc_id"]
    outsider = login(client, "reviewer", org="Elsewhere")
    assert client.get(f"/kyc/verifications/{kid}", headers=outsider).status_code == 404
    assert client.get("/kyc/review/queue", headers=outsider).json() == []


# --- what KYC gates ---------------------------------------------------------------------------


def test_payments_auto_approval_can_require_kyc(client):
    _pay_policy(client, kyc_required_for_auto_approve=True)
    c, _, _ = _state_after_statement_first(client)
    assert c["state"] == "FINANCIALLY_CONFIRMED"  # clear in every other way, but no KYC on file


def test_payments_auto_approval_proceeds_once_the_payer_is_verified(client):
    _pay_policy(client, kyc_required_for_auto_approve=True, **KYC_ON)
    assert _submit(client, login(client, "submitter"))["state"] == "VERIFIED"  # learner-1
    c, _, _ = _state_after_statement_first(client)
    assert c["state"] == "APPROVED" and "AUTO_APPROVED" in c["reason_codes"]


def test_the_payment_rule_is_listed_for_replay():
    from app.policy.engine import PolicyConfig
    from app.review.auto import Inputs, rule_failures

    i = Inputs("ecocash", "GREEN", "E3", "statement", 0, False, False, 4000, "USD", 4000, 4000)
    cfg = PolicyConfig(
        auto_approve_enabled=True,
        auto_approve_channels=["ecocash"],
        kyc_required_for_auto_approve=True,
    )
    assert rule_failures(cfg, i) == ["KYC_NOT_VERIFIED"]
    i.kyc_verified = True
    assert rule_failures(cfg, i) == []
    assert (
        rule_failures(
            PolicyConfig(auto_approve_enabled=True, auto_approve_channels=["ecocash"]),
            Inputs("ecocash", "GREEN", "E3", "statement", 0, False, False, 4000, "USD", 4000, 4000),
        )
        == []
    )


def test_staff_gate_blocks_unverified_approvers_but_never_admins(client):
    _policy(client, kyc_required_for_staff=True, kyc_auto_verify_subjects=["payer", "staff"])
    appr = login(client, "approver", "boss@example.com")
    body = {"reason": "test"}
    url = f"/allocations/{uuid.uuid4()}/reverse"
    r = client.post(url, headers=appr, json=body)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "KYC_REQUIRED"
    # an admin is never locked out (they must be able to turn the policy off)
    assert client.post(url, headers=login(client, "admin"), json=body).status_code == 404
    # the approver verifies themselves, automatically, then passes the gate
    assert _submit(client, appr, subject_type="staff", subject_ref=None)["state"] == "VERIFIED"
    assert client.post(url, headers=appr, json=body).status_code == 404
