"""KYC building blocks: MRZ, templates, agreement, card detection, face matcher, the rules."""

import io
import shutil
from datetime import date

import cv2
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

from app import devtools
from app.extraction.schema import FieldReading
from app.kyc import agreement, templates
from app.kyc.auto import Inputs, rule_failures
from app.kyc.cv import face
from app.kyc.cv.docintegrity import portrait_copy_flag
from app.kyc.doc import classify, detect, mrz
from app.policy.engine import PolicyConfig

GOOD = dict(
    country="ZWE",
    surname="Moyo",
    given="Tendai Grace",
    number="AB1234567",
    nationality="ZWE",
    dob=date(1995, 3, 14),
    sex="F",
    expiry=date(2032, 6, 30),
)


# --- MRZ ---------------------------------------------------------------------------------------


def test_td3_roundtrip_and_fields():
    r = mrz.read("\n".join(mrz.compose_td3(**GOOD)))
    assert r and r.valid and r.format == "TD3" and r.doc_type == "passport"
    assert (r.full_name, r.date_of_birth, r.expiry_date) == (
        "TENDAI GRACE MOYO",
        "1995-03-14",
        "2032-06-30",
    )


def test_edited_digit_breaks_a_check_digit():
    l1, l2 = mrz.compose_td3(**GOOD)
    forged = l2[:13] + "ZWE9503146F3206309" + l2[31:]  # expiry day changed, digit not updated
    r = mrz.read(f"{l1}\n{forged}")
    assert r is not None and not r.valid


def test_noisy_ocr_text_still_finds_the_block():
    l1, l2 = mrz.compose_td3(**GOOD)
    text = f"REPUBLIC OF ZIMBABWE\nPASSPORT\n{l1[:20]} {l1[20:]}\n{l2.lower()}\nsome footer"
    r = mrz.read(text)
    assert r is not None and r.valid


def test_ocr_letter_for_digit_in_a_date_is_repaired_then_checked():
    l1, l2 = mrz.compose_td3(**GOOD)
    r = mrz.read(f"{l1}\n{l2[:13]}95O314{l2[19:]}")  # a 0 read as the letter O in the DOB
    assert r is not None and r.checks["date_of_birth"]


def test_td1_id_card():
    l1 = "I<ZWE" + "AB1234567" + "<" * 0
    num = "AB1234567"
    l1 = ("I<ZWE" + num + str(mrz.check_digit(num))).ljust(30, "<")
    dob, exp = "950314", "320630"
    l2 = (dob + str(mrz.check_digit(dob)) + "F" + exp + str(mrz.check_digit(exp)) + "ZWE").ljust(
        29, "<"
    )
    comp = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    l2 += str(mrz.check_digit(comp))
    l3 = "MOYO<<TENDAI<GRACE".ljust(30, "<")
    r = mrz.read("\n".join([l1, l2, l3]))
    assert r and r.format == "TD1" and r.valid and r.doc_type == "national_id"


@given(
    number=st.from_regex(r"[0-9]{9}", fullmatch=True),
    dob=st.dates(min_value=date(1940, 1, 1), max_value=date(2005, 12, 31)),
    expiry=st.dates(min_value=date(2026, 1, 1), max_value=date(2040, 12, 31)),
)
def test_valid_mrz_always_parses_and_any_digit_edit_is_caught(number, dob, expiry):
    lines = mrz.compose_td3(**{**GOOD, "number": number, "dob": dob, "expiry": expiry})
    assert mrz.parse(lines).valid  # type: ignore[union-attr]
    # change one digit of the document number: its check digit must now fail
    i = 3
    edited = lines[1][:i] + str((int(lines[1][i]) + 1) % 10) + lines[1][i + 1 :]
    assert not mrz.parse([lines[0], edited]).valid  # type: ignore[union-attr]


# --- templates ---------------------------------------------------------------------------------


def test_parse_text_reads_labelled_lines():
    out = templates.parse_text(
        "REPUBLIC OF ZIMBABWE\nNATIONAL ID\nSURNAME: MOYO\nGIVEN NAMES: TENDAI GRACE\n"
        "ID NO: 63-123456 K 42\nDATE OF BIRTH: 14/03/1995\nDATE OF EXPIRY: 30/06/2032\nSEX: F"
    )
    assert out["surname"] == "MOYO" and out["given_names"] == "TENDAI GRACE"
    assert out["document_number"] == "63-123456 K 42" and out["sex"] == "F"
    assert out["issuing_country"] == "ZWE" and out["doc_type"] == "national_id"
    assert out["date_of_birth"] == "14/03/1995"


@pytest.mark.parametrize(
    ("country", "number", "ok"),
    [
        ("ZWE", "63-123456 K 42", True),
        ("ZWE", "6312", False),
        ("ZAF", "8001015009087", True),
        ("ZAF", "8001015009088", False),
        ("KEN", "12345678", True),
        ("XYZ", "anything", None),
    ],
)
def test_number_format(country, number, ok):
    assert templates.number_format_ok(country, "national_id", number) is ok


def test_number_format_has_no_opinion_on_passports():
    assert templates.number_format_ok("ZWE", "passport", "AB123") is None


def test_classify_prefers_a_valid_mrz_over_reader_votes():
    r = mrz.read("\n".join(mrz.compose_td3(**GOOD)))
    assert classify.classify(r, ["national_id"]) == "passport"
    assert classify.classify(None, ["Identity card", "id", None]) == "national_id"
    assert classify.classify(None, []) is None


# --- agreement ---------------------------------------------------------------------------------


def _fr(**kv):
    return {k: FieldReading(k, v, 0.8) for k, v in kv.items()}


def test_two_sources_agreeing_on_all_critical_fields():
    f = devtools.id_fields()
    r = agreement.combine(
        {"a": _fr(**f), "b": _fr(**{**f, "full_name": "Moyo Tendai Grace"})}, None
    )
    assert r.agreement == 1.0 and not r.conflicts
    assert r.fields["document_number"] == "63123456K42"
    assert r.fields["date_of_birth"] == "1995-03-14"


def test_a_missing_middle_name_is_fine_a_different_name_is_not():
    f = devtools.id_fields()
    ok = agreement.combine({"a": _fr(**f), "b": _fr(**{**f, "full_name": "TENDAI MOYO"})}, None)
    assert ok.agreed["full_name"]
    bad = agreement.combine({"a": _fr(**f), "b": _fr(**{**f, "full_name": "PETER NCUBE"})}, None)
    assert not bad.agreed["full_name"] and "full_name" in bad.conflicts


def test_one_source_alone_is_not_agreement():
    r = agreement.combine({"a": _fr(**devtools.id_fields())}, None)
    assert r.agreement == 0.0


def test_a_valid_mrz_counts_as_a_source_and_a_contradicting_reader_is_a_conflict():
    m = mrz.read("\n".join(mrz.compose_td3(**GOOD)))
    printed = {
        "full_name": "TENDAI GRACE MOYO",
        "document_number": "AB1234567",
        "date_of_birth": "14/03/1995",
        "expiry_date": "30/06/2032",
    }
    ok = agreement.combine({"vlm": _fr(**printed)}, m)
    assert ok.agreement == 1.0
    edited = agreement.combine({"vlm": _fr(**{**printed, "expiry_date": "30/06/2040"})}, m)
    assert "expiry_date" in edited.conflicts and not edited.agreed["expiry_date"]


# --- card detection ----------------------------------------------------------------------------


def test_find_card_flattens_a_skewed_card_on_a_dark_table():
    card = np.asarray(Image.open(io.BytesIO(devtools.make_id_card(None))).convert("RGB"))
    scene = np.full((1100, 1500, 3), 25, np.uint8)
    h, w = card.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[230, 160], [1210, 210], [1170, 800], [190, 760]])
    warped = cv2.warpPerspective(card, cv2.getPerspectiveTransform(src, dst), (1500, 1100))
    mask = cv2.warpPerspective(
        np.full(card.shape[:2], 255, np.uint8), cv2.getPerspectiveTransform(src, dst), (1500, 1100)
    )
    scene[mask > 0] = warped[mask > 0]
    found = detect.find_card(Image.fromarray(scene))
    assert found.found and found.method in ("hough", "contour")
    assert abs(found.image.width / found.image.height - 1.585) < 0.35


def test_find_card_falls_back_to_the_frame():
    flat = Image.new("RGB", (800, 600), (128, 128, 128))
    found = detect.find_card(flat)
    assert not found.found and found.method == "frame"


def test_capture_quality_flags_blur_small_and_glare():
    blurred = Image.fromarray(
        cv2.GaussianBlur(
            np.asarray(Image.open(io.BytesIO(devtools.make_id_card(None))).convert("RGB")),
            (0, 0),
            12,
        )
    )
    assert "CAPTURE_BLURRY" in detect.capture_quality(blurred, 300, 40).problems
    assert (
        "CAPTURE_TOO_SMALL" in detect.capture_quality(Image.new("RGB", (100, 100)), 300, 0).problems
    )
    assert (
        "CAPTURE_GLARE"
        in detect.capture_quality(Image.new("RGB", (800, 600), "white"), 300, 0).problems
    )


# --- face (fake backend) -----------------------------------------------------------------------


def _img(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def test_same_person_scores_high_different_person_low():
    card = _img(devtools.make_id_card(None, face_seed=1))
    m = face.FakeFaceMatcher()
    same = face.match(m, card, _img(devtools.make_selfie(1)))
    other = face.match(m, card, _img(devtools.make_selfie(2)))
    assert same.score and same.score > 0.8 and other.score is not None and other.score < 0.3


def test_no_face_is_reported_not_scored():
    card = _img(devtools.make_id_card(None, blank_face=True))
    r = face.match(face.FakeFaceMatcher(), card, _img(devtools.make_selfie(1)))
    assert r.score is None and "NO_FACE_ON_ID" in r.problems
    r = face.match(
        face.FakeFaceMatcher(),
        _img(devtools.make_id_card(None)),
        _img(devtools.make_selfie(blank=True)),
    )
    assert r.score is None and "NO_FACE_IN_SELFIE" in r.problems


def test_a_selfie_that_is_the_id_photo_is_flagged():
    card = _img(devtools.make_id_card(None, face_seed=1))
    r = face.match(face.FakeFaceMatcher(), card, _img(devtools.make_selfie(1, variation=0.0)))
    assert portrait_copy_flag(r.portrait_phash_source, r.selfie_face) == [
        "SELFIE_COPIED_FROM_ID_PHOTO"
    ]
    r = face.match(face.FakeFaceMatcher(), card, _img(devtools.make_selfie(1)))
    assert portrait_copy_flag(r.portrait_phash_source, r.selfie_face) == []


def test_onnx_backend_without_a_model_fails_closed(monkeypatch, tmp_path):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "kyc_face_backend", "onnx")
    monkeypatch.setattr(s, "kyc_face_model_path", str(tmp_path / "missing.onnx"))
    with pytest.raises(face.FaceUnavailable):
        face.build_matcher()


# --- the rules ---------------------------------------------------------------------------------


def _cfg(**over) -> PolicyConfig:
    return PolicyConfig(kyc_auto_verify_mode="on", kyc_allowed_countries=["ZWE"], **over)


def _inputs(**over) -> Inputs:
    base = dict(
        subject_type="payer",
        doc_type="national_id",
        issuing_country="ZWE",
        mrz_valid=None,
        ocr_agreement=1.0,
        conflicts=[],
        days_to_expiry=900,
        number_format_ok=True,
        face_score=0.9,
        liveness_score=1.0,
        tamper_score=0.0,
        integrity_flags=[],
        readers_ok=True,
    )
    return Inputs(**{**base, **over})


def test_a_clear_case_has_no_blockers():
    assert rule_failures(_cfg(), _inputs()) == []


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"doc_type": None}, "DOC_TYPE_UNKNOWN"),
        ({"doc_type": "drivers_licence"}, None),
        ({"issuing_country": "KEN"}, "COUNTRY_NOT_ALLOWED"),
        ({"issuing_country": None}, "COUNTRY_UNKNOWN"),
        ({"mrz_valid": False}, "MRZ_CHECKSUM_FAIL"),
        ({"doc_type": "passport", "mrz_valid": None}, "MRZ_REQUIRED"),
        ({"ocr_agreement": 0.75}, "LOW_OCR_AGREEMENT"),
        ({"ocr_agreement": None}, "LOW_OCR_AGREEMENT"),
        ({"conflicts": ["expiry_date"]}, "FIELD_CONFLICT"),
        ({"days_to_expiry": -1}, "DOC_EXPIRED"),
        ({"days_to_expiry": 10}, "DOC_EXPIRES_SOON"),
        ({"days_to_expiry": None}, "EXPIRY_UNKNOWN"),
        ({"number_format_ok": False}, "NUMBER_FORMAT_INVALID"),
        ({"face_score": 0.2}, "FACE_SCORE_LOW"),
        ({"face_score": None}, "FACE_UNAVAILABLE"),
        ({"liveness_score": 0.3}, "LIVENESS_LOW"),
        ({"liveness_score": None}, "LIVENESS_UNAVAILABLE"),
        ({"tamper_score": 0.9}, "DOC_TAMPER_SUSPECTED"),
        ({"tamper_score": None}, "TAMPER_CHECK_SKIPPED"),
        (
            {"integrity_flags": ["ID_NUMBER_USED_BY_OTHER_SUBJECT"]},
            "ID_NUMBER_USED_BY_OTHER_SUBJECT",
        ),
        ({"readers_ok": False}, "READERS_UNAVAILABLE"),
        ({"subject_type": "staff"}, "SUBJECT_NOT_ENABLED"),
    ],
)
def test_each_rule_blocks(over, code):
    failures = rule_failures(_cfg(), _inputs(**over))
    if code is None:  # a licence is allowed by default
        assert failures == []
    else:
        assert code in failures


def test_off_and_kill_switch_always_block():
    assert "DISABLED" in rule_failures(PolicyConfig(), _inputs())
    assert "KILL_SWITCH" in rule_failures(_cfg(), _inputs(), kill_switch=True)


def test_policy_refuses_auto_verify_without_countries():
    with pytest.raises(ValueError, match="allowed_countries"):
        PolicyConfig(kyc_auto_verify_mode="on")
    with pytest.raises(ValueError):
        PolicyConfig(kyc_auto_verify_mode="sometimes")
    with pytest.raises(ValueError):
        PolicyConfig(kyc_allowed_countries=["ZW"])


def test_ocr_that_drops_trailing_filler_on_the_name_line_still_parses():
    l1, l2 = mrz.compose_td3(**GOOD)
    r = mrz.read(f"junk\n{l1.rstrip('<')}\n{l2}\n")
    assert r is not None and r.valid and r.full_name == "TENDAI GRACE MOYO"


def test_a_data_line_that_lost_characters_is_refused_not_guessed():
    l1, l2 = mrz.compose_td3(**GOOD)
    assert mrz.read(f"{l1}\n{l2[:30]}") is None


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_real_ocr_reads_a_rendered_passport_page():
    from PIL import ImageDraw, ImageFont

    from app.kyc.readers import IdOCRReader

    def font(size):
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except OSError:
            return ImageFont.load_default(size)

    img = Image.new("RGB", (1000, 700), (245, 245, 240))
    d = ImageDraw.Draw(img)
    for i, t in enumerate(
        [
            "PASSPORT",
            "SURNAME: MOYO",
            "GIVEN NAMES: TENDAI GRACE",
            "PASSPORT NO: AB1234567",
            "DATE OF BIRTH: 14/03/1995",
            "DATE OF EXPIRY: 30/06/2032",
            "SEX: F",
        ]
    ):
        d.text((60, 40 + i * 56), t, fill="black", font=font(34))
    try:
        mono = ImageFont.truetype("DejaVuSansMono.ttf", 33)
    except OSError:
        pytest.skip("monospace font not available")
    for i, t in enumerate(mrz.compose_td3(**GOOD)):
        d.text((20, 560 + i * 50), t, fill="black", font=mono)
    got = IdOCRReader().read(img, b"")
    assert got["surname"].raw == "MOYO" and got["document_number"].raw == "AB1234567"
    parsed = mrz.read(got["mrz"].raw)
    assert parsed is not None and parsed.valid
    combined = agreement.combine({"id_ocr": got}, parsed)
    assert combined.agreement == 1.0 and not combined.conflicts
