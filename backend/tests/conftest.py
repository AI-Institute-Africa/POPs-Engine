import os
import tempfile
import uuid
from collections.abc import Iterator

os.environ.setdefault("POPS_ENV", "test")
os.environ.setdefault("POPS_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/pops_test")
os.environ["POPS_VLM_BACKEND"] = "fake"
os.environ["POPS_PIPELINE_MODE"] = "inline"
os.environ["POPS_STORAGE_BACKEND"] = "local"
os.environ["POPS_STORAGE_LOCAL_DIR"] = tempfile.mkdtemp(prefix="pops-evidence-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    from app.core import ratelimit

    backend = ratelimit.backend()
    if isinstance(backend, ratelimit._Memory):
        backend.reset()
    yield
    with get_engine().begin() as conn:
        tables = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ',') FROM pg_tables "
                "WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        ).scalar()
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def session():
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, role: str, email: str | None = None, org: str = "Cognify") -> dict:
    email = email or f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    r = client.post("/auth/dev-login", json={"email": email, "role": role, "org_name": org})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


from app.devtools import ecocash_fields, make_receipt, statement_csv  # noqa: E402,F401
