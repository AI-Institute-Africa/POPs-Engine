"""Optional request signing for alert forwarders.

The source key travels as a bearer header, so anyone who sees one request can replay it. Signing
binds each request to a timestamp and its body: the forwarder sends

    X-POPs-Timestamp: <unix seconds>
    X-POPs-Signature: hex(HMAC-SHA256(secret, f"{timestamp}." + body))

where `secret` is the SHA-256 hex digest of the source key (the server stores only that digest,
never the key). A request outside the time window, or with a wrong signature, is refused. When a
signature is present it is always checked; `POPS_ALERT_REQUIRE_SIGNATURE` makes it mandatory.
"""

import hashlib
import hmac

WINDOW_SECONDS = 300


class SignatureError(Exception):
    """The signature is missing, malformed, stale or wrong."""


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def secret_for_key(key: str) -> str:
    """What a forwarder computes from its key; equals AlertSource.key_hash."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float,
    *,
    required: bool,
    window: int = WINDOW_SECONDS,
) -> None:
    """Raises SignatureError unless the request is acceptable."""
    if not timestamp and not signature:
        if required:
            raise SignatureError("signed requests are required: send timestamp and signature")
        return
    if not timestamp or not signature:
        raise SignatureError("send both X-POPs-Timestamp and X-POPs-Signature")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("X-POPs-Timestamp must be unix seconds") from exc
    if abs(now - ts) > window:
        raise SignatureError("request timestamp is outside the allowed window")
    if not hmac.compare_digest(sign(secret, ts, body), signature.strip().lower()):
        raise SignatureError("bad signature")
