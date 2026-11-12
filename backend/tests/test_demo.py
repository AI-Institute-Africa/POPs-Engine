"""The standalone demo: every story must do what it says, and keep doing it."""

import io
import random

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.core.config import Settings, get_settings, validate_for_startup
from app.demo import art, service
from app.demo import scenarios as S
from app.demo.api import router as demo_router
from app.demo.sim import DemoFaceMatcher
from app.kyc.cv import face
from app.kyc.doc import detect
from app.main import app
from app.models import Claim, KycVerification

# the demo endpoints are only mounted in demo builds; mount them on the shared test app once
if not any(getattr(r, "path", "").startswith("/demo") for r in app.routes):
    app.include_router(demo_router)


@pytest.fixture
def api(monkeypatch) -> S.Api:
    monkeypatch.setattr(get_settings(), "kyc_face_backend", "demo")
    a = service.api_for(TestClient(app))
    a.call("admin", "POST", "/admin/policy", json={"version": S.POLICY_VERSION, "config": S.POLICY})
    return a


@pytest.fixture
def demo_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "kyc_face_backend", "demo")
    c = TestClient(app)
    service.api_for(c).call(
        "admin", "POST", "/admin/policy", json={"version": S.POLICY_VERSION, "config": S.POLICY}
    )
    return c


# --- every story does what it says ---------------------------------------------------------------


@pytest.mark.parametrize("scenario", S.SCENARIOS, ids=lambda s: s.id)
def test_scenario_outcome(api, scenario):
    r = scenario.run(api)
    assert r.state == scenario.outcome, (r.state, r.reasons)
    if scenario.reason:
        assert scenario.reason in r.reasons, r.reasons
    assert r.steps and r.headline


def test_stories_can_be_replayed_over_and_over(api):
    """A second tour must not trip the duplicate / reuse checks the first one set up."""
    for _ in range(3):
        for sid in ("pay-clear", "kyc-clear", "kyc-passport", "pay-blurry"):
            sc = S.BY_ID[sid]
            r = sc.run(api)
            assert r.state == sc.outcome and sc.reason in r.reasons, (sid, r.state, r.reasons)


def test_receipt_before_money_is_approved_once_the_money_arrives(api):
    r = S.BY_ID["pay-waiting"].run(api)
    assert r.state == "AWAITING_FINANCIAL_CONFIRMATION" and len(r.followups) == 1
    after = S.deliver_money(api, **r.followups[0]["params"])
    assert after.state == "APPROVED" and "AUTO_APPROVED" in after.reasons


def test_the_payer_is_never_told_about_suspected_fraud(api):
    r = S.BY_ID["pay-edited"].run(api)
    assert r.payer_messages and all("fake" not in m.lower() for m in r.payer_messages)
    assert "SUSPECTED_FAKE" in r.reasons  # staff see it; the payer does not
    alerts = api.call("admin", "GET", "/fraud/alerts")
    assert alerts and any(a["claim_id"] == r.ref_id for a in alerts)


def test_a_mismatch_is_never_rejected_automatically(api):
    r = S.BY_ID["kyc-mismatch"].run(api)
    assert r.state == "NEEDS_REVIEW"  # a person decides; nothing is auto-rejected
    d = api.call(
        "reviewer",
        "POST",
        f"/kyc/review/{r.ref_id}/decision",
        json={"outcome": "REJECTED", "note": "selfie is a different person"},
    )
    assert d["state"] == "REJECTED"


# --- the simulated pieces are honest about what they can tell apart ------------------------------


def _img(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_simulated_face_match_separates_people_across_many_photos():
    m = DemoFaceMatcher()
    rnd = random.Random(1)
    keys = list(art.PERSONAS)
    same, diff = [], []
    for k in keys:
        card = detect.find_card(
            _img(art.id_card_png(art.id_fields(k, "VD-1000001"), k, seed=rnd.randrange(10**6)))
        )
        assert card.found, f"card detection failed for {k}"
        for _ in range(4):
            same.append(
                face.match(
                    m, card.image, _img(art.selfie_png(k, seed=rnd.randrange(1, 10**6)))
                ).score
            )
        for other in keys:
            if other != k:
                diff.append(
                    face.match(
                        m, card.image, _img(art.selfie_png(other, seed=rnd.randrange(1, 10**6)))
                    ).score
                )
    threshold = S.POLICY["kyc_min_face_score"]
    assert min(same) > threshold + 0.08, min(same)
    assert max(diff) < threshold - 0.05, max(diff)


def test_no_face_is_reported_not_scored():
    m = DemoFaceMatcher()
    card = detect.find_card(_img(art.id_card_png(art.id_fields("tendai", "VD-1"), "tendai")))
    r = face.match(m, card.image, _img(art.selfie_png(None)))
    assert r.score is None and "NO_FACE_IN_SELFIE" in r.problems
    blank = detect.find_card(
        _img(art.id_card_png(art.id_fields("tendai", "VD-1"), "tendai", blank_face=True))
    )
    assert "NO_FACE_ON_ID" in face.match(m, blank.image, _img(art.selfie_png("tendai"))).problems


def test_new_receipts_are_never_near_duplicates_of_stored_ones():
    known: list[int] = []
    for i in range(25):
        png = art.distinct_receipt(
            {
                "transaction_id": f"PP{i:04d}.1432.A1",
                "amount": "40.00",
                "currency": "USD",
                "provider": "EcoCash",
                "status": "Successful",
                "sender": "A B",
                "beneficiary": "MAIN",
                "date": "01/01/2026 10:00",
            },
            known,
        )
        h = art.phash_int(png)
        assert all(art.hamming(h, k) >= 10 for k in known)
        known.append(h)


def test_samples_say_sample_and_use_a_fictional_country():
    assert art.COUNTRY == "VDA"
    from app.kyc.doc import mrz

    png = art.passport_png("farai", "VP1234567")
    assert _img(png).width > 1000
    chunk = Image.open(io.BytesIO(png)).info["pops-id-ocr"]
    import json

    lines = json.loads(chunk)["mrz"].split("\n")
    parsed = mrz.parse(lines)
    assert parsed is not None and parsed.valid and parsed.issuing_country == "VDA"


# --- endpoints -----------------------------------------------------------------------------------


def test_info_lists_every_scenario(demo_client):
    r = demo_client.get("/demo/info").json()
    assert r["demo"] and {s["id"] for s in r["scenarios"]} == {s.id for s in S.SCENARIOS}
    assert {p["role"] for p in r["personas"]} == {"submitter", "reviewer", "approver", "admin"}


def test_run_endpoint_and_unknown_scenario(demo_client):
    r = demo_client.post("/demo/scenarios/pay-blurry/run")
    assert r.status_code == 200 and r.json()["state"] == "NEEDS_INFORMATION"
    assert demo_client.post("/demo/scenarios/nope/run").status_code == 404


def test_try_it_yourself_payment_flow_end_to_end(demo_client):
    """What the console's sample button does: prepare, download the file, submit by hand."""
    prep = demo_client.post("/demo/prepare/payment/matched").json()
    png = demo_client.get(prep["file_url"].removeprefix("/demo") and prep["file_url"]).content
    assert png.startswith(b"\x89PNG")
    a = service.api_for(demo_client)
    ob = a.call("admin", "GET", "/obligations")
    inv = next(o for o in ob if o["payment_code"] == prep["payment_code"])
    claim = a.call(
        "payer",
        "POST",
        "/claims",
        data={
            "obligation_id": inv["obligation_id"],
            "claimant_ref": prep["claimant_ref"],
            "channel": prep["channel"],
        },
        files={"file": (prep["filename"], png, "image/png")},
    )
    assert a.call("reviewer", "GET", f"/claims/{claim['claim_id']}")["state"] == "APPROVED"
    assert demo_client.post("/demo/prepare/payment/nope").status_code == 404


def test_try_it_yourself_identity_flow(demo_client):
    prep = demo_client.post("/demo/prepare/kyc/clear").json()
    front = demo_client.get(prep["front_url"]).content
    selfie = demo_client.get(prep["selfie_url"]).content
    a = service.api_for(demo_client)
    out = a.call(
        "payer",
        "POST",
        "/kyc/verifications",
        data={"subject_type": "payer", "subject_ref": prep["subject_ref"]},
        files={"front": ("f.png", front, "image/png"), "selfie": ("s.png", selfie, "image/png")},
    )
    assert out["state"] == "VERIFIED"
    assert demo_client.get("/demo/files/doesnotexist").status_code == 404


def test_reset_wipes_and_reseeds(demo_client, session, monkeypatch):
    monkeypatch.setattr(S, "SEED_ORDER", ["pay-clear", "kyc-clear"])
    S.BY_ID["pay-blurry"].run(service.api_for(demo_client))
    assert session.scalar(select(func.count()).select_from(Claim)) >= 1
    session.rollback()  # end this read transaction: TRUNCATE needs every table lock
    r = demo_client.post("/demo/reset")
    assert r.status_code == 200 and r.json()["seeded"] == {"pay-clear": 1, "kyc-clear": 1}
    session.expire_all()
    # the blurry claim is gone; the seeded story and its identity check are there
    states = [c.state for c in session.scalars(select(Claim))]
    assert states == ["APPROVED"]
    assert session.scalar(select(func.count()).select_from(KycVerification)) == 1


# --- packaging -----------------------------------------------------------------------------------


def test_demo_mode_is_refused_in_production():
    s = Settings(
        _env_file=None,
        env="prod",
        demo=True,
        jwt_secret="x" * 40,
        clamav_host="clamav",
        vlm_backend="ollama",
        pipeline_mode="celery",
        kyc_face_backend="onnx",
    )
    with pytest.raises(Exception, match="POPS_DEMO"):
        validate_for_startup(s)
    s2 = Settings(
        _env_file=None,
        env="prod",
        jwt_secret="x" * 40,
        clamav_host="clamav",
        vlm_backend="ollama",
        pipeline_mode="celery",
        kyc_face_backend="demo",
    )
    with pytest.raises(Exception, match="KYC_FACE_BACKEND=demo"):
        validate_for_startup(s2)


def test_single_port_server_serves_console_and_api(tmp_path):
    from app.demo.server import build

    (tmp_path / "index.html").write_text("<html>console</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("1")
    c = TestClient(build(tmp_path))
    assert c.get("/").text == "<html>console</html>"
    assert (
        c.get("/kyc/review").text == "<html>console</html>"
    )  # client-side route survives a refresh
    assert c.get("/assets/a.js").text == "1"
    assert c.get("/api/health").json() == {"status": "ok"}


def test_server_without_a_built_console_says_how_to_build_it():
    from app.demo.server import build

    c = TestClient(build(None))
    assert "npm" in c.get("/").json()["message"]
    assert c.get("/api/health").status_code == 200
