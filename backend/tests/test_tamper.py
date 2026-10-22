"""Tamper forensics (blueprint 8): deterministic risk flags that escalate but never decide."""

import io

from PIL import Image

from app.devtools import ecocash_fields, make_receipt, splice_receipt
from app.extraction import tamper
from tests.conftest import login
from tests.test_flow import TXN, _import, _obligation, _stmt_row, _submit


def _img(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def test_clean_receipt_scores_low():
    report = tamper.analyse(_img(make_receipt(ecocash_fields(), seed=7)))
    assert report.skipped is None
    assert report.score < 0.6, report.as_dict()
    assert report.regions == []


def test_tiny_image_is_skipped_not_flagged():
    report = tamper.analyse(Image.new("RGB", (80, 80), "white"))
    assert report.skipped == "image_too_small"
    assert report.score == 0.0


def test_analysis_is_deterministic():
    png = make_receipt(ecocash_fields(), seed=11)
    assert tamper.analyse(_img(png)).as_dict() == tamper.analyse(_img(png)).as_dict()


def test_spliced_receipt_scores_higher_with_regions():
    base = make_receipt(ecocash_fields(), seed=3)
    donor = make_receipt(ecocash_fields(txn="PP251021.1432.B99999"), seed=99)
    spliced = splice_receipt(base, donor, box=(120, 300, 400, 620))
    clean = tamper.analyse(_img(base))
    dirty = tamper.analyse(_img(spliced))
    assert dirty.score > clean.score, (clean.as_dict(), dirty.as_dict())
    assert dirty.score >= 0.6, dirty.as_dict()
    assert dirty.regions, dirty.as_dict()
    # the biggest flagged region should overlap the pasted box (normalised 0..1)
    box = dirty.regions[0]
    assert 0.0 <= box["x"] <= box["x"] + box["w"] <= 1.0
    assert 0.0 <= box["y"] <= box["y"] + box["h"] <= 1.0
    overlap_x = box["x"] < 400 / 720 and box["x"] + box["w"] > 120 / 720
    overlap_y = box["y"] < 620 / 1280 and box["y"] + box["h"] > 300 / 1280
    assert overlap_x and overlap_y, box


def test_tampered_claim_goes_to_review_even_with_a_matching_credit(client):
    """A manipulation flag routes to a reviewer; it never confirms on its own (blueprint 8)."""
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    base = make_receipt(ecocash_fields(), seed=5)
    donor = make_receipt(ecocash_fields(txn="PP251021.1432.C77777"), seed=51)
    spliced = splice_receipt(base, donor, box=(120, 300, 400, 620))
    claim = _submit(
        client,
        sub,
        ob,
        spliced,
        declared_amount="40.00",
        declared_reference=TXN,
    )
    # even with a statement line that matches, the flag keeps the claim out of confirmation
    _import(client, rev, [_stmt_row(txn=TXN)])
    claim = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert "TAMPER_SUSPECTED" in claim["reason_codes"]
    assert claim["state"] in ("PENDING_REVIEW", "DUPLICATE_HOLD", "NEEDS_INFORMATION")
    assert claim["approval_tier"] != "GREEN"

    detail = client.get(f"/review/claims/{claim['claim_id']}", headers=rev).json()
    assert detail["tamper"] is not None
    assert detail["tamper"]["check"] == "tamper"


def test_tamper_check_disabled_by_policy(client):
    """Policy is configuration, not code: an org can switch the forensics off."""
    sub, adm = login(client, "submitter"), login(client, "admin")
    r = client.post(
        "/admin/policy",
        headers=adm,
        json={"version": "no-tamper", "config": {"tamper_enabled": False}},
    )
    assert r.status_code == 201, r.text

    ob = _obligation(client, sub)
    base = make_receipt(ecocash_fields(), seed=13)
    donor = make_receipt(ecocash_fields(txn="PP251021.1432.D33333"), seed=52)
    spliced = splice_receipt(base, donor, box=(120, 300, 400, 620))
    claim = _submit(client, sub, ob, spliced, declared_amount="40.00")
    claim = client.get(f"/claims/{claim['claim_id']}", headers=sub).json()
    assert "TAMPER_SUSPECTED" not in claim["reason_codes"]
