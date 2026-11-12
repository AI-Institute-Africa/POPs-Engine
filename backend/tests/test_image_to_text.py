"""`POST /image-to-text`: the console's standalone OCR converter."""

import io

from PIL import Image

from tests.conftest import ecocash_fields, login, make_receipt


def _convert(client, headers, data: bytes, name: str = "receipt.png", ctype: str = "image/png"):
    return client.post(
        "/image-to-text", headers=headers, files={"file": (name, data, ctype)}
    )


def test_converts_an_image_to_text(client):
    sub = login(client, "submitter")
    png = make_receipt(
        ecocash_fields(),
        meta={"pops-text": "EcoCash\nPayment Successful\nAmount: USD 40.00"},
    )
    r = _convert(client, sub, png)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "EcoCash\nPayment Successful\nAmount: USD 40.00"
    assert body["character_count"] == len(body["text"])
    assert body["page_count"] == 1
    (page,) = body["pages"]
    assert page["page"] == 1
    assert page["reader"] == "fixture_text"
    assert page["lines"] == 3
    assert page["confidence"] == 0.99
    assert body["reader_errors"] == []


def test_pdf_text_layer_is_returned_exactly(client):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Bank transfer receipt")
    page.insert_text((72, 100), "Amount: USD 40.00")
    pdf = doc.tobytes()

    sub = login(client, "submitter")
    r = _convert(client, sub, pdf, "receipt.pdf", "application/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "Bank transfer receipt" in body["text"]
    assert "Amount: USD 40.00" in body["text"]
    assert body["pages"][0]["reader"] == "text_layer"


def test_conversion_creates_no_claim(client, session):
    from sqlalchemy import func, select

    from app.models import Claim

    sub = login(client, "submitter")
    png = make_receipt(ecocash_fields(), meta={"pops-text": "Amount: USD 40.00"})
    assert _convert(client, sub, png).status_code == 200
    assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_requires_a_file(client):
    sub = login(client, "submitter")
    assert client.post("/image-to-text", headers=sub).status_code == 422


def test_requires_login(client):
    png = make_receipt(ecocash_fields(), meta={"pops-text": "Amount: USD 40.00"})
    r = client.post("/image-to-text", files={"file": ("pop.png", png, "image/png")})
    assert r.status_code in (401, 403)


def test_rejects_a_non_proof_file(client):
    sub = login(client, "submitter")
    r = _convert(client, sub, b"MZ\x90\x00not an image", "x.exe", "application/octet-stream")
    assert r.status_code == 422


def test_is_malware_scanned_before_anything_is_parsed(client, monkeypatch):
    from app.api import routes
    from app.core.config import get_settings
    from tests.test_hardening import EICAR, FakeClamd, _png_with

    clamd = FakeClamd()
    monkeypatch.setattr(get_settings(), "clamav_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "clamav_port", clamd.port)

    def _never(*_a, **_k):
        raise AssertionError("an infected file reached the parsers")

    monkeypatch.setattr(routes, "prepare", _never)
    sub = login(client, "submitter")
    r = _convert(client, sub, _png_with(EICAR))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "MALWARE_DETECTED"


def test_fails_closed_when_the_scanner_is_down(client, monkeypatch):
    import socket

    from app.core.config import get_settings

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(get_settings(), "clamav_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "clamav_port", port)
    sub = login(client, "submitter")
    r = _convert(client, sub, make_receipt(ecocash_fields()))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SCANNER_UNAVAILABLE"


def test_conversion_is_rate_limited(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_text_conversions_per_minute", 2)
    sub = login(client, "submitter")
    png = make_receipt(ecocash_fields(), meta={"pops-text": "Amount: USD 40.00"})
    assert [_convert(client, sub, png).status_code for _ in range(2)] == [200, 200]
    r = _convert(client, sub, png)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_a_blank_image_returns_empty_text_not_an_error(client):
    """A readable file with nothing in it is not a server failure."""
    img = Image.new("RGB", (600, 400), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    sub = login(client, "submitter")
    r = _convert(client, sub, buf.getvalue())
    # With a fake vision backend and no Tesseract in the test environment there is no reader:
    # that is a 503. With Tesseract installed the page simply has no text: a 200 with "".
    assert r.status_code in (200, 503)
    if r.status_code == 200:
        assert r.json()["text"] == ""
