"""`POST /claims/preview`: the payer checks legibility before submitting a claim."""

from tests.conftest import ecocash_fields, login, make_receipt


def _preview(client, headers, *pngs: bytes):
    files = [("file", (f"pop{i}.png", png, "image/png")) for i, png in enumerate(pngs)]
    return client.post("/claims/preview", headers=headers, files=files or None)


def test_legible_receipt_is_readable_and_lists_fields(client):
    sub = login(client, "submitter")
    r = _preview(client, sub, make_receipt(ecocash_fields()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["readable"] is True
    (item,) = body["files"]
    assert item["readable"] is True and item["quality_issues"] == []
    fields = {f["name"]: f["value"] for f in item["fields"]}
    assert fields["amount"] and fields["transaction_id"]


def test_blurry_receipt_is_not_readable(client):
    sub = login(client, "submitter")
    r = _preview(client, sub, make_receipt(ecocash_fields(), blur=12))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["readable"] is False
    assert body["files"][0]["quality_issues"]


def test_one_bad_file_makes_the_set_unreadable(client):
    sub = login(client, "submitter")
    r = _preview(
        client, sub, make_receipt(ecocash_fields()), make_receipt(ecocash_fields(), blur=12)
    )
    body = r.json()
    assert [f["readable"] for f in body["files"]] == [True, False]
    assert body["readable"] is False


def test_preview_creates_no_claim(client, session):
    from sqlalchemy import func, select

    from app.models import Claim

    sub = login(client, "submitter")
    assert _preview(client, sub, make_receipt(ecocash_fields())).status_code == 200
    assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_preview_needs_a_file_and_at_most_five(client):
    sub = login(client, "submitter")
    assert _preview(client, sub).status_code == 422
    png = make_receipt(ecocash_fields())
    assert _preview(client, sub, *([png] * 6)).status_code == 422


def test_preview_rejects_a_non_proof_file(client):
    sub = login(client, "submitter")
    r = client.post(
        "/claims/preview",
        headers=sub,
        files={"file": ("x.exe", b"MZ\x90\x00not a receipt", "application/octet-stream")},
    )
    assert r.status_code == 422


def test_preview_requires_login(client):
    r = client.post(
        "/claims/preview",
        files={"file": ("pop.png", make_receipt(ecocash_fields()), "image/png")},
    )
    assert r.status_code in (401, 403)


def test_preview_is_malware_scanned_before_anything_is_parsed(client, monkeypatch):
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
    r = _preview(client, sub, _png_with(EICAR))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "MALWARE_DETECTED"


def test_preview_fails_closed_when_the_scanner_is_down(client, monkeypatch):
    import socket

    from app.core.config import get_settings

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(get_settings(), "clamav_host", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "clamav_port", port)
    sub = login(client, "submitter")
    r = _preview(client, sub, make_receipt(ecocash_fields()))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SCANNER_UNAVAILABLE"


def test_preview_is_rate_limited(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_previews_per_minute", 2)
    sub = login(client, "submitter")
    png = make_receipt(ecocash_fields())
    assert [_preview(client, sub, png).status_code for _ in range(2)] == [200, 200]
    r = _preview(client, sub, png)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
