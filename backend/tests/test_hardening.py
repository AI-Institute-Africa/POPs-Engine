"""Production hardening: config safety, OIDC, limits, scanning, resilience, OCR, observability."""

import io
import json
import socket
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select, update

from app.core import security
from app.core.config import ConfigError, Settings, get_settings, validate_for_startup
from app.core.db import get_sessionmaker
from app.models import Claim, VerificationRun
from app.normalise.fields import date_candidates
from app.policy.engine import PolicyConfig
from app.workers import tasks
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit

# --- configuration --------------------------------------------------------------------------


def test_defaults_are_production_and_refuse_unsafe_boot(monkeypatch):
    monkeypatch.delenv("POPS_ENV")
    monkeypatch.delenv("POPS_VLM_BACKEND")
    monkeypatch.delenv("POPS_PIPELINE_MODE")
    s = Settings(_env_file=None)
    assert s.env == "prod" and not s.is_dev
    with pytest.raises(ConfigError) as e:
        validate_for_startup(s)
    msg = str(e.value)
    assert "POPS_JWT_SECRET" in msg and "POPS_CLAMAV_HOST" in msg


def test_safe_production_config_boots(monkeypatch):
    monkeypatch.delenv("POPS_ENV")
    monkeypatch.delenv("POPS_VLM_BACKEND")
    monkeypatch.delenv("POPS_PIPELINE_MODE")
    s = Settings(_env_file=None, jwt_secret="x" * 40, clamav_host="clamav")
    validate_for_startup(s)


def test_dev_login_is_hidden_outside_dev(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "env", "prod")
    r = client.post("/auth/dev-login", json={"email": "a@b.c", "role": "admin"})
    assert r.status_code == 404


# --- OIDC ------------------------------------------------------------------------------------


@pytest.fixture
def oidc(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = get_settings()
    monkeypatch.setattr(s, "oidc_issuer", "https://id.cognify.test")
    monkeypatch.setattr(s, "oidc_audience", "pops")
    monkeypatch.setattr(s, "oidc_jwks_url", "https://id.cognify.test/jwks")

    class _Key:
        def __init__(self, k):
            self.key = k

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key(key.public_key())

    monkeypatch.setattr(security, "_jwks_client", lambda url: _Client())
    org = uuid.uuid4()

    def token(role="reviewer", amr=("pwd", "mfa"), iss="https://id.cognify.test"):
        now = int(time.time())
        claims = {
            "sub": "user-123",
            "iss": iss,
            "aud": "pops",
            "exp": now + 600,
            "iat": now,
            "pops_role": role,
            "pops_org": str(org),
            "amr": list(amr),
        }
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256")}

    return token


def test_oidc_token_accepted_with_mfa(client, oidc):
    r = client.get("/me", headers=oidc())
    assert r.status_code == 200 and r.json()["role"] == "reviewer"


def test_oidc_finance_role_without_mfa_refused(client, oidc):
    assert client.get("/me", headers=oidc(amr=("pwd",))).status_code == 403
    # submitters (e.g. Cognify's backend) do not need MFA
    assert client.get("/me", headers=oidc(role="submitter", amr=())).status_code == 200


def test_oidc_wrong_issuer_refused(client, oidc):
    assert client.get("/me", headers=oidc(iss="https://evil.test")).status_code == 401


# --- policy limits ---------------------------------------------------------------------------


def test_second_approver_limits_are_per_currency():
    cfg = PolicyConfig()
    assert not cfg.needs_second_approver(40_000, "USD")
    assert cfg.needs_second_approver(60_000, "USD")
    assert cfg.needs_second_approver(100, "ZWG")  # no ZWG limit configured: fail closed
    assert PolicyConfig(second_approver_above_minor=1000).second_approver_above_minor == {
        "USD": 1000
    }


def test_zig_claim_needs_two_approvers_until_finance_sets_a_limit(client):
    sub, rev, appr = (
        login(client, "submitter"),
        login(client, "reviewer"),
        login(client, "approver"),
    )
    ob = _obligation(client, sub, currency="ZWG")
    _import(client, rev, [_stmt_row(currency="ZWG")])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(currency="ZWG")))
    cid = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()["candidates"][0][
        "credit"
    ]["credit_id"]
    first = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=rev,
        json={"credit_id": cid, "txn_last4": "2345"},
    ).json()
    assert first["needs_second_approval"]
    second = client.post(
        f"/review/claims/{claim['claim_id']}/confirm",
        headers=appr,
        json={"credit_id": cid, "txn_last4": "2345"},
    )
    assert second.json()["claim"]["state"] == "APPROVED"


def test_manual_credit_role_comes_from_policy(client):
    admin, rev = login(client, "admin"), login(client, "reviewer")
    body = {
        "provider": "ECOCASH",
        "provider_txn_id": "MAN0001",
        "amount_minor": 100,
        "currency": "USD",
        "occurred_at": "2026-10-21T12:33:00Z",
        "beneficiary": "MAIN",
        "attestation": "Seen on merchant portal by finance officer",
    }
    assert client.post("/credits", headers=rev, json=body).status_code == 403
    r = client.post(
        "/admin/policy",
        headers=admin,
        json={"version": "v-reviewer-credits", "config": {"manual_credit_min_role": "reviewer"}},
    )
    assert r.status_code == 201, r.text
    assert client.post("/credits", headers=rev, json=body).status_code == 201


# --- uploads: malware scan and rate limits ---------------------------------------------------

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamd:
    """Speaks enough clamd INSTREAM to test the client: flags EICAR, passes everything else."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while not buf.endswith(b"\0\0\0\0"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                conn.sendall(
                    b"stream: Eicar-Signature FOUND\0" if EICAR in buf else b"stream: OK\0"
                )


def _png_with(payload: bytes) -> bytes:
    img = make_receipt(ecocash_fields())
    return img + payload  # trailing bytes after IEND still decode as PNG


def test_malware_is_rejected_and_clean_files_pass(client, monkeypatch):
    clamd = FakeClamd()
    monkeypatch.setattr(get_settings(), "clamav_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "clamav_port", clamd.port)
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    bad = client.post(
        "/claims",
        headers=sub,
        data={"obligation_id": ob["obligation_id"], "claimant_ref": "x"},
        files={"file": ("p.png", _png_with(EICAR), "image/png")},
    )
    assert bad.status_code == 422 and "MALWARE_DETECTED" in bad.text
    ok = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert ok["state"] == "AWAITING_FINANCIAL_CONFIRMATION"


def test_scanner_down_fails_closed(client, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(get_settings(), "clamav_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "clamav_port", port)
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    r = client.post(
        "/claims",
        headers=sub,
        data={"obligation_id": ob["obligation_id"], "claimant_ref": "x"},
        files={"file": ("p.png", make_receipt(ecocash_fields()), "image/png")},
    )
    assert r.status_code == 503 and r.headers["Retry-After"] == "30"


def test_upload_rate_limits(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_claims_per_claimant_per_day", 2)
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    for _ in range(2):
        _submit(client, sub, ob, make_receipt(ecocash_fields(txn=uuid.uuid4().hex[:10])))
    r = client.post(
        "/claims",
        headers=sub,
        data={"obligation_id": ob["obligation_id"], "claimant_ref": "learner-1"},
        files={"file": ("p.png", make_receipt(ecocash_fields()), "image/png")},
    )
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


# --- resilience ------------------------------------------------------------------------------


def test_pipeline_failures_are_counted_then_handed_to_review(client, monkeypatch):
    from app.workers import pipeline

    sub = login(client, "submitter")
    ob = _obligation(client, sub)

    def boom(*a, **k):
        raise RuntimeError("model server exploded")

    monkeypatch.setattr(pipeline, "process_claim", boom)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    assert claim["state"] == "RECEIVED"  # upload still accepted; nothing lost
    for _ in range(2):
        assert tasks.run_pipeline(uuid.UUID(claim["claim_id"])) is False
    final = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert final["state"] == "PENDING_REVIEW" and final["reason_codes"] == ["PIPELINE_FAILED"]


def test_sweeper_reprocesses_stuck_claims(client, monkeypatch):
    from app.workers import pipeline

    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    real = pipeline.process_claim
    monkeypatch.setattr(pipeline, "process_claim", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    monkeypatch.setattr(pipeline, "process_claim", real)
    with get_sessionmaker()() as s:
        s.execute(
            update(Claim)
            .where(Claim.claim_id == uuid.UUID(claim["claim_id"]))
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )
        s.commit()
    stuck = client.get("/ops/stuck", headers=login(client, "admin")).json()
    assert [c["attempts"] for c in stuck] == [1]
    assert client.get("/ops/stuck", headers=login(client, "admin", org="Other")).json() == []
    assert tasks.sweep_stuck() == 1
    assert (
        client.get(f"/claims/{claim['claim_id']}", headers=sub).json()["state"]
        == "AWAITING_FINANCIAL_CONFIRMATION"
    )


def test_import_reports_unknown_rematch_count_when_queued(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "pipeline_mode", "celery")
    monkeypatch.setattr(tasks.rematch_task, "delay", lambda *a: None)
    rev = login(client, "reviewer")
    r = _import(client, rev, [_stmt_row()])
    assert r.status_code == 201 and r.json()["rematched_claims"] is None


# --- matching edge cases ---------------------------------------------------------------------


def test_near_duplicate_found_in_sql(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    png = make_receipt(ecocash_fields(), seed=7)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.putpixel((5, 5), (250, 0, 0))  # different bytes, same picture
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    _submit(client, sub, ob, png)
    second = client.post(
        "/claims",
        headers=sub,
        data={"obligation_id": ob["obligation_id"], "claimant_ref": "learner-1"},
        files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")},
    ).json()
    assert "DUPLICATE_NEAR_FILE" in second["reason_codes"]


def test_ambiguous_dates():
    assert [d.month for d in date_candidates("04/10/2026")] == [10, 4]
    assert [d.month for d in date_candidates("04/10/2026", order="MDY")] == [4, 10]
    assert [d.month for d in date_candidates("10/21/2026")] == [10]  # only valid month-first
    assert len(date_candidates("21/10/2026")) == 1


def test_month_first_receipt_still_matches_via_alternative_reading(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    # receipt printed 10/04/2026 by an app that is month-first; money arrived 4 October
    _import(client, rev, [_stmt_row(date="04/10/2026 09:00")])
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields(date="10/04/2026 08:55")))
    assert claim["state"] == "FINANCIALLY_CONFIRMED"
    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    gate = next(g for g in detail["candidates"][0]["gates"] if g["name"] == "date_window")
    assert "alternative reading" in gate["detail"]


def test_missing_currency_is_flagged(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    fields = ecocash_fields()
    fields.pop("currency")
    claim = _submit(client, sub, ob, make_receipt(fields))
    assert "CURRENCY_NOT_ON_RECEIPT" in claim["reason_codes"]


def test_correction_moves_claim_forward(client):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    claim = _submit(
        client,
        sub,
        ob,
        make_receipt(ecocash_fields(), ocr=ecocash_fields(txn="PP251021.1432.A12845")),
    )
    assert claim["state"] == "PENDING_REVIEW"
    client.post(
        f"/review/claims/{claim['claim_id']}/fields",
        headers=rev,
        json={"name": "transaction_id", "value": TXN, "reason": "checked on screenshot"},
    )
    after = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert after["state"] == "FINANCIALLY_CONFIRMED"
    assert "EXTRACTION_UNCONFIRMED" not in after["reason_codes"]


# --- configurable layouts --------------------------------------------------------------------


def test_templates_and_profiles_load_from_files(tmp_path, monkeypatch):
    from app.credits import statements
    from app.extraction import templates

    tf = tmp_path / "t.json"
    tf.write_text(
        json.dumps(
            [
                {
                    "provider": "MYBANK",
                    "document_type": "bank_transfer",
                    "keywords": ["mybank"],
                    "txn_patterns": [r"\b(MB\d{8})\b"],
                    "date_order": "MDY",
                }
            ]
        )
    )
    pf = tmp_path / "p.json"
    pf.write_text(
        json.dumps(
            [
                {
                    "name": "mybank",
                    "provider": "MYBANK",
                    "date_col": "When",
                    "narrative_col": "Details",
                    "amount_col": "Amt",
                    "currency": "USD",
                }
            ]
        )
    )
    monkeypatch.setattr(get_settings(), "provider_templates_file", str(tf))
    monkeypatch.setattr(get_settings(), "statement_profiles_file", str(pf))
    templates.templates.cache_clear()
    statements.profiles.cache_clear()
    try:
        assert (
            templates.parse_text("MyBank transfer ref MB12345678")["transaction_id"] == "MB12345678"
        )
        assert templates.template_for("MYBANK").date_order == "MDY"
        assert "mybank" in statements.profiles() and "generic" in statements.profiles()
    finally:
        templates.templates.cache_clear()
        statements.profiles.cache_clear()


# --- real OCR path ---------------------------------------------------------------------------


def _font(size: int):
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    pytest.skip("no TrueType font available")


def test_tesseract_reads_a_rendered_receipt():
    from app.extraction.readers.base import ReaderUnavailable

    try:
        from app.extraction.readers.tesseract_ocr import TesseractReader

        reader = TesseractReader()
    except ReaderUnavailable:
        pytest.skip("tesseract not installed")
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    lines = [
        "EcoCash",
        "Payment Successful",
        f"Txn ID: {TXN}",
        "Amount: USD 40.00",
        "Paid to: COGNIFY MAIN",
        "21/10/2026 14:32",
    ]
    for i, line in enumerate(lines):
        d.text((40, 40 + i * 90), line, fill="black", font=_font(40))
    out = reader.read(img, b"")
    assert out["provider"].raw == "ECOCASH"
    assert out["transaction_id"].raw == TXN
    assert out["amount"].raw == "40.00"
    assert out["status"].raw.lower() == "successful"
    assert out["amount"].region is not None  # word boxes feed the console overlay


# --- observability ---------------------------------------------------------------------------


def test_metrics_and_request_ids(client):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    _submit(client, sub, ob, make_receipt(ecocash_fields()))
    m = client.get("/metrics")
    assert m.status_code == 200 and "pops_claim_outcomes_total" in m.text
    r = client.get("/health", headers={"X-Request-Id": "abc123"})
    assert r.headers["X-Request-Id"] == "abc123"


def test_verification_run_records_guardrail_set(client, session):
    sub = login(client, "submitter")
    ob = _obligation(client, sub)
    claim = _submit(client, sub, ob, make_receipt(ecocash_fields()))
    run = session.scalars(
        select(VerificationRun).where(VerificationRun.claim_id == uuid.UUID(claim["claim_id"]))
    ).one()
    assert run.model_versions["guardrails"] == []
    assert any(c["check"] == "guardrails" for c in run.checks)
