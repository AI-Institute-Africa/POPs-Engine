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


def splice_receipt(
    base: bytes, patch: bytes, box: tuple[int, int, int, int], *, seed: int = 20261021
) -> bytes:
    """Pretend a crop from `patch` was pasted into `base` at `box` (tamper-test fixture).

    Forensically realistic: the donor carries its own sensor-noise floor (Gaussian, seeded)
    and a different JPEG history - exactly the inconsistency tamper forensics look for - and
    the base gets the mild capture noise of a real photo so the two floors are comparable.
    The base's tEXt chunks (fake-reader ground truth) are carried over.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    src = Image.open(io.BytesIO(base))
    info = PngInfo()
    for k, v in src.info.items():
        if isinstance(v, str):
            info.add_text(k, v)
    canvas = np.asarray(src.convert("RGB"), dtype=np.float32)
    canvas += rng.normal(0.0, 2.0, canvas.shape)  # the base photo's own capture noise
    donor = Image.open(io.BytesIO(patch)).convert("RGB").crop(box)
    buf = io.BytesIO()
    donor.save(buf, format="JPEG", quality=90)  # a different compression history
    buf.seek(0)
    darr = np.asarray(Image.open(buf).convert("RGB"), dtype=np.float32)
    darr += rng.normal(0.0, 12.0, darr.shape)  # the pasted crop's own (higher) noise floor
    x0, y0 = box[0], box[1]
    h = min(darr.shape[0], canvas.shape[0] - y0)
    w = min(darr.shape[1], canvas.shape[1] - x0)
    canvas[y0 : y0 + h, x0 : x0 + w] = darr[:h, :w]
    out = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))
    obuf = io.BytesIO()
    out.save(obuf, format="PNG", pnginfo=info)
    return obuf.getvalue()


def statement_csv(rows: list[dict]) -> bytes:
    header = "date,narrative,reference,credit,debit,currency\n"
    body = "".join(
        f"{r['date']},{r.get('narrative', '')},{r.get('reference', '')},"
        f"{r.get('credit', '')},{r.get('debit', '')},{r.get('currency', 'USD')}\n"
        for r in rows
    )
    return (header + body).encode()
