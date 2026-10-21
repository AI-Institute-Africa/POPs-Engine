import io
import json
import os
import random
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
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
from PIL.PngImagePlugin import PngInfo  # noqa: E402
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


def make_receipt(
    vlm: dict | None,
    ocr: dict | None = None,
    *,
    lines: list[str] | None = None,
    size: tuple[int, int] = (720, 1280),
    blur: float = 0.0,
    seed: int | None = None,
) -> bytes:
    """Synthetic receipt PNG. Readers' 'ground truth' rides in tEXt chunks (see FakeVLMReader)."""
    rnd = random.Random(seed if seed is not None else uuid.uuid4().int)
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    # random blocks make every synthetic receipt perceptually distinct
    for _ in range(12):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        w, h = rnd.randrange(40, 250), rnd.randrange(40, 250)
        d.rectangle([x, y, x + w, y + h], fill=tuple(rnd.randrange(256) for _ in range(3)))
    for i, line in enumerate(lines or [f"{k}: {v}" for k, v in (vlm or {}).items()]):
        d.text((40, 60 + i * 40), str(line), fill="black")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    info = PngInfo()
    if vlm is not None:
        info.add_text("pops-vlm", json.dumps(vlm))
    if ocr is not None:
        info.add_text("pops-ocr", json.dumps(ocr))
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def ecocash_fields(txn: str = "PP251021.1432.A12345", amount: str = "40.00", **over) -> dict:
    base = {
        "provider": "EcoCash",
        "document_type": "mobile_money_receipt",
        "transaction_id": txn,
        "amount": amount,
        "currency": "USD",
        "date": "21/10/2026 14:32",
        "sender": "Tendai Moyo",
        "beneficiary": "MAIN",
        "status": "Successful",
    }
    base.update(over)
    return base


def statement_csv(rows: list[dict]) -> bytes:
    header = "date,narrative,reference,credit,debit,currency\n"
    body = "".join(
        f"{r['date']},{r.get('narrative', '')},{r.get('reference', '')},"
        f"{r.get('credit', '')},{r.get('debit', '')},{r.get('currency', 'USD')}\n"
        for r in rows
    )
    return (header + body).encode()
