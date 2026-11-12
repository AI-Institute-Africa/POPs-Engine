"""Synthetic POP fixtures shared by tests and the demo seeder. Not used in production paths."""

import io
import json
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from PIL import Image, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo

# Fixture receipts are dated "yesterday" so they stay inside the fraud rules' windows (not in the
# future, not days before the invoice) whenever the suite runs.
DAY: date = datetime.now(UTC).date() - timedelta(days=1)


def dmy(hm: str = "14:32", days: int = 0) -> str:
    """`DD/MM/YYYY HH:MM` on the fixture day, shifted by `days`."""
    return f"{DAY + timedelta(days=days):%d/%m/%Y} {hm}"


def iso_z(hm: str = "12:33", days: int = 0) -> str:
    return f"{DAY + timedelta(days=days):%Y-%m-%d}T{hm}:00Z"


def rfc2822(hm: str = "14:40") -> str:
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[DAY.weekday()]
    month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[DAY.month - 1]
    return f"{weekday}, {DAY.day:02d} {month} {DAY.year} {hm}:00 +0200"


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
        "date": dmy("14:32"),
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
        if isinstance(k, str) and isinstance(v, str):
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


# --- KYC fixtures ---------------------------------------------------------------------------------


def _face_pattern(seed: int, variation: float = 0.0, variation_seed: int = 0) -> Any:
    """A deterministic 8x8 'face': same seed = same person. `variation` re-randomises that
    fraction of the cells, as a different pose or light would."""
    import numpy as np

    grid = np.random.RandomState(seed).randint(40, 220, (8, 8))
    if variation:
        rs = np.random.RandomState(variation_seed + 7919)
        mask = rs.rand(8, 8) < variation
        grid = np.where(mask, rs.randint(40, 220, (8, 8)), grid)
    return grid.astype("uint8")


def make_id_card(
    vlm: dict | None,
    ocr: dict | None = None,
    *,
    face_seed: int = 1,
    size: tuple[int, int] = (1000, 630),
    blank_face: bool = False,
    mrz: list[str] | None = None,
    seed: int | None = None,
) -> bytes:
    """Synthetic ID card PNG. The FakeFaceMatcher reads the portrait from a fixed region and the
    fake ID readers read their 'ground truth' from tEXt chunks (`pops-id-vlm`, `pops-id-ocr`)."""
    import numpy as np

    rnd = random.Random(seed if seed is not None else uuid.uuid4().int)  # noqa: S311
    img = Image.new("RGB", size, (236, 240, 228))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, size[0] - 5, size[1] - 5], outline=(30, 60, 90), width=6)
    # unique background texture, so every fixture is perceptually distinct (duplicate checks)
    for _ in range(40):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.rectangle(
            [x, y, x + rnd.randrange(8, 40), y + rnd.randrange(8, 40)],
            fill=tuple(rnd.randrange(150, 255) for _ in range(3)),
        )
    px, py, pw, ph = 0.05, 0.25, 0.30, 0.55
    box = (int(px * size[0]), int(py * size[1]), int((px + pw) * size[0]), int((py + ph) * size[1]))
    if not blank_face:
        cells = np.kron(
            _face_pattern(face_seed), np.ones((int(ph * size[1] / 8), int(pw * size[0] / 8)))
        )
        patch = Image.fromarray(cells.astype("uint8")).convert("RGB")
        img.paste(patch.resize((box[2] - box[0], box[3] - box[1])), box[:2])
    else:
        d.rectangle(box, fill=(236, 240, 228))
    for i, line in enumerate((vlm or {}).items()):
        d.text((int(0.42 * size[0]), 120 + i * 38), f"{line[0]}: {line[1]}", fill="black")
    info = PngInfo()
    data_ocr = dict(ocr) if ocr is not None else None
    if mrz and data_ocr is not None:
        data_ocr["mrz"] = "\n".join(mrz)
    if vlm is not None:
        info.add_text("pops-id-vlm", json.dumps(vlm))
    if data_ocr is not None:
        info.add_text("pops-id-ocr", json.dumps(data_ocr))
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def make_selfie(
    face_seed: int = 1, *, variation: float = 0.2, size: int = 640, blank: bool = False
) -> bytes:
    """Synthetic selfie: the same 'person' as the card with the same seed, slightly changed."""
    import numpy as np

    if blank:
        img = Image.new("RGB", (size, size), (200, 200, 200))
    else:
        cells = np.kron(_face_pattern(face_seed, variation, 3), np.ones((size // 8, size // 8)))
        img = Image.fromarray(cells.astype("uint8")).convert("RGB")
        noise = np.random.RandomState(5).randint(-6, 6, (size, size, 3))
        img = Image.fromarray(np.clip(np.asarray(img).astype(int) + noise, 0, 255).astype("uint8"))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def id_fields(
    *,
    name: str = "TENDAI GRACE MOYO",
    number: str = "63-123456 K 42",
    dob: str = "14/03/1995",
    expiry: str | None = None,
    country: str = "ZWE",
    doc_type: str = "national_id",
) -> dict:
    exp = expiry or f"30/06/{datetime.now(UTC).year + 6}"
    return {
        "doc_type": doc_type,
        "issuing_country": country,
        "full_name": name,
        "document_number": number,
        "date_of_birth": dob,
        "expiry_date": exp,
        "sex": "F",
    }
