"""Malware scanning through a clamd daemon using the INSTREAM protocol (blueprint 4.1 step 1).

Fails closed: if scanning is configured but the daemon cannot be reached, the upload is refused
with a retryable error rather than stored unscanned. In dev/test with no daemon configured the
scan is skipped (production refuses to start without one; see validate_for_startup).
"""

import logging
import socket
import struct

from app.core.config import get_settings

log = logging.getLogger(__name__)
CHUNK = 64 * 1024


class InfectedFile(Exception):
    def __init__(self, signature: str):
        super().__init__(signature)
        self.signature = signature


class ScannerUnavailable(Exception):
    pass


def scan(data: bytes) -> str:
    """Return 'clean' or 'skipped'; raise InfectedFile or ScannerUnavailable."""
    s = get_settings()
    if not s.clamav_host:
        return "skipped"
    try:
        with socket.create_connection(
            (s.clamav_host, s.clamav_port), s.clamav_timeout_seconds
        ) as c:
            c.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), CHUNK):
                part = data[i : i + CHUNK]
                c.sendall(struct.pack("!L", len(part)) + part)
            c.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                buf = c.recv(4096)
                if not buf:
                    break
                reply += buf
    except OSError as exc:
        raise ScannerUnavailable(f"clamd unreachable: {exc}") from exc
    text = reply.rstrip(b"\0").decode(errors="replace")
    if text.endswith("OK"):
        return "clean"
    if text.endswith("FOUND"):
        sig = text.split(":", 1)[-1].removesuffix("FOUND").strip()
        raise InfectedFile(sig)
    raise ScannerUnavailable(f"unexpected clamd reply: {text[:200]}")
