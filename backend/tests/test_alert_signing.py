"""Alert request signing: pure checks, no database."""

import pytest

from app.alerts import signing

KEY = "pops_ak_example"
SECRET = signing.secret_for_key(KEY)
BODY = b'{"from":"EcoCash","text":"hello"}'
NOW = 1_800_000_000.0


def _ok(ts=int(NOW), body=BODY, **kw):
    sig = signing.sign(SECRET, ts, BODY)
    signing.verify(SECRET, str(ts), sig, body, NOW, required=kw.get("required", True))


def test_valid_signature_passes():
    _ok()


def test_unsigned_allowed_unless_required():
    signing.verify(SECRET, None, None, BODY, NOW, required=False)
    with pytest.raises(signing.SignatureError):
        signing.verify(SECRET, None, None, BODY, NOW, required=True)


def test_half_a_signature_is_refused_even_when_optional():
    with pytest.raises(signing.SignatureError):
        signing.verify(SECRET, str(int(NOW)), None, BODY, NOW, required=False)
    with pytest.raises(signing.SignatureError):
        signing.verify(SECRET, None, "abc", BODY, NOW, required=False)


def test_tampered_body_is_refused():
    with pytest.raises(signing.SignatureError):
        _ok(body=BODY + b" ")


def test_wrong_secret_is_refused():
    sig = signing.sign("other", int(NOW), BODY)
    with pytest.raises(signing.SignatureError):
        signing.verify(SECRET, str(int(NOW)), sig, BODY, NOW, required=True)


def test_stale_and_future_timestamps_are_refused():
    for ts in (int(NOW) - signing.WINDOW_SECONDS - 1, int(NOW) + signing.WINDOW_SECONDS + 1):
        with pytest.raises(signing.SignatureError):
            _ok(ts=ts)


def test_edge_of_window_passes_and_garbage_timestamp_fails():
    _ok(ts=int(NOW) - signing.WINDOW_SECONDS)
    with pytest.raises(signing.SignatureError):
        signing.verify(SECRET, "yesterday", "abc", BODY, NOW, required=True)


def test_signature_case_is_ignored():
    sig = signing.sign(SECRET, int(NOW), BODY).upper()
    signing.verify(SECRET, str(int(NOW)), sig, BODY, NOW, required=True)
