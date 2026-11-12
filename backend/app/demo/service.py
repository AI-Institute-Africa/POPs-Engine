"""Demo housekeeping: first-run seeding, reset, and a small cache for sample files."""

import logging
from collections import OrderedDict

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import get_engine, get_sessionmaker
from app.demo import scenarios as S
from app.models import Evidence, Organisation

log = logging.getLogger(__name__)


def known_hashes() -> list[int]:
    """Perceptual hashes already stored, so new sample receipts are never near-duplicates."""
    with get_sessionmaker()() as s:
        rows = s.execute(
            select(Evidence.phash_int).where(Evidence.phash_int.is_not(None)).limit(3000)
        )
        return [h for (h,) in rows if h is not None]


def api_for(http: httpx.Client) -> S.Api:
    return S.Api(http, known_hashes)


def is_empty(session: Session) -> bool:
    return session.scalar(select(Organisation.org_id).limit(1)) is None


def wipe() -> None:
    """Remove every row (the schema stays). TRUNCATE does not fire the append-only triggers."""
    with get_engine().begin() as conn:
        tables = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ',') FROM pg_tables "
                "WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        ).scalar()
        if tables:
            conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    backend = ratelimit.backend()
    if isinstance(backend, ratelimit._Memory):
        backend.reset()


def seed(api: S.Api) -> dict[str, int]:
    """Create the demo organisation, its policy, and enough activity that every page has content."""
    api.call(
        "admin", "POST", "/admin/policy", json={"version": S.POLICY_VERSION, "config": S.POLICY}
    )
    ran: dict[str, int] = {}
    for sid in S.SEED_ORDER:
        try:
            S.BY_ID[sid].run(api)
            ran[sid] = ran.get(sid, 0) + 1
        except S.DemoError:
            log.exception("seed scenario %s failed", sid)
    return ran


class FileCache:
    """Generated sample files served once by id (the try-it-yourself buttons)."""

    def __init__(self, size: int = 80):
        self.size = size
        self._d: OrderedDict[str, bytes] = OrderedDict()

    def put(self, key: str, data: bytes) -> str:
        self._d[key] = data
        while len(self._d) > self.size:
            self._d.popitem(last=False)
        return key

    def get(self, key: str) -> bytes | None:
        return self._d.get(key)


FILES = FileCache()
