"""WhatsApp Business Cloud API intake."""

import hashlib
import hmac
import json

import httpx
import pytest

from app.core.config import get_settings
from app.whatsapp import service as wa
from tests.conftest import ecocash_fields, login, make_receipt
from tests.test_documents import RECEIPT_TEXT, _internal
from tests.test_flow import _import, _obligation, _stmt_row

SECRET = "app-secret-for-tests"
SENDER = "263771234567"


@pytest.fixture
def graph(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_verify_token", "verify-me")
    monkeypatch.setattr(s, "whatsapp_app_secret", SECRET)
    monkeypatch.setattr(s, "whatsapp_access_token", "token")
    monkeypatch.setattr(s, "whatsapp_phone_number_id", "1234")
    sent: list[dict] = []
    media = {"m1": make_receipt(ecocash_fields())}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer token"
        path = req.url.path
        if path.endswith("/1234/messages"):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"messages": [{"id": "out"}]})
        if path.startswith("/media/"):
            return httpx.Response(200, content=media[path.rsplit("/", 1)[-1]])
        media_id = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"url": f"https://lookaside.test/media/{media_id}"})

    monkeypatch.setattr(
        wa,
        "_http",
        lambda: httpx.Client(
            transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer token"}
        ),
    )
    return sent


def _post(client, *messages, secret=SECRET):
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": list(messages)}}]}]}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def _image(mid, caption=None, media="m1"):
    return {
        "id": mid,
        "from": SENDER,
        "type": "image",
        "image": {"id": media, **({"caption": caption} if caption else {})},
    }


def _text(mid, body):
    return {"id": mid, "from": SENDER, "type": "text", "text": {"body": body}}


def test_verify_handshake(client, graph):
    q = "hub.mode=subscribe&hub.challenge=42&hub.verify_token="
    assert client.get(f"/whatsapp/webhook?{q}verify-me").text == "42"
    assert client.get(f"/whatsapp/webhook?{q}wrong").status_code == 403


def test_unsigned_webhooks_are_refused(client, graph):
    assert _post(client, _text("w0", "hi"), secret="forged").status_code == 401


def test_not_configured_is_404(client):
    assert (
        client.get(
            "/whatsapp/webhook?hub.mode=subscribe&hub.challenge=1&hub.verify_token=x"
        ).status_code
        == 404
    )


def test_photo_with_code_in_caption_becomes_a_claim(client, graph):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _import(client, rev, [_stmt_row()])
    r = _post(client, _image("w1", f"Fees for Tendai {ob['payment_code']}"))
    assert r.status_code == 200 and r.json()["claims"] == 1, r.text
    claims = client.get("/claims", headers=rev).json()
    assert claims[0]["channel"] == "whatsapp"
    assert claims[0]["state"] == "FINANCIALLY_CONFIRMED", claims[0]["reason_codes"]
    assert "Received your proof of payment" in graph[-1]["text"]["body"]
    assert graph[-1]["to"] == SENDER
    # Meta redelivers: the same message id creates nothing new
    assert _post(client, _image("w1", ob["payment_code"])).json()["claims"] == 0
    assert len(client.get("/claims", headers=rev).json()) == 1


def test_code_then_photo_are_joined(client, graph):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    _post(client, _text("w2", ob["payment_code"]))
    assert "Now send the proof" in graph[-1]["text"]["body"]
    r = _post(client, _image("w3"))
    assert r.json()["claims"] == 1
    claim = client.get("/claims", headers=rev).json()[0]
    assert claim["obligation_id"] == ob["obligation_id"]


def test_forwarded_payment_sms_as_text(client, graph):
    sub, rev = login(client, "submitter"), login(client, "reviewer")
    ob = _obligation(client, sub)
    r = _post(client, _text("w4", "\n".join([ob["payment_code"], *RECEIPT_TEXT])))
    assert r.json()["claims"] == 1
    claim = client.get("/claims", headers=rev).json()[0]
    signals = {s["code"] for s in _internal(client, claim["claim_id"])["risk_signals"]}
    assert "PASTED_TEXT" in signals


def test_photo_without_any_code_gets_instructions(client, graph):
    r = _post(client, _image("w5"))
    assert r.json()["claims"] == 0
    assert "payment code" in graph[-1]["text"]["body"]
