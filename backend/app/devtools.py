"""Synthetic POP fixtures shared by tests and the demo seeder. Not used in production paths."""

import io
import json
import random
import uuid

from PIL import Image, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo


def make_receipt(
    vlm: dict | None,
    ocr: dict | None = None,
    *,
    lines: list[str] | None = None,
    size: tuple[int, int] = (720, 1280),
    blur: float = 0.0,
    seed: int | None = None,
    meta: dict[str, str] | None = None,
) -> bytes:
    """Synthetic receipt PNG. Readers' 'ground truth' rides in tEXt chunks (see FakeVLMReader)."""
    rnd = random.Random(seed if seed is not None else uuid.uuid4().int)  # noqa: S311
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
    for key, value in (meta or {}).items():
        info.add_text(key, value)  # e.g. {"Software": "Adobe Photoshop"} to simulate an editor
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
