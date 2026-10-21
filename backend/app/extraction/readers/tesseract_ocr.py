"""Second, independent reader: Tesseract OCR plus the deterministic provider templates.

It also supplies word boxes so the reviewer console can highlight where each value was read.
"""

import re
import shutil
from dataclasses import dataclass

from PIL import Image

from app.extraction import preprocess, templates
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.schema import FieldReading


@dataclass
class Word:
    text: str
    conf: float
    box: tuple[int, int, int, int]  # x, y, w, h


class TesseractReader:
    name = "tesseract_ocr"

    def __init__(self) -> None:
        if shutil.which("tesseract") is None:
            raise ReaderUnavailable("tesseract binary not installed")

    @property
    def version(self) -> str:
        import pytesseract

        return f"tesseract:{pytesseract.get_tesseract_version()}"

    def words(self, image: Image.Image) -> tuple[list[Word], str, tuple[int, int]]:
        import pytesseract

        prepared = preprocess.for_ocr(image)
        data = pytesseract.image_to_data(prepared, output_type=pytesseract.Output.DICT)
        words: list[Word] = []
        lines: dict[tuple[int, int, int], list[str]] = {}
        for i, text in enumerate(data["text"]):
            if not text.strip():
                continue
            conf = float(data["conf"][i])
            box = (data["left"][i], data["top"][i], data["width"][i], data["height"][i])
            words.append(Word(text, conf, box))
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(text)
        full = "\n".join(" ".join(ws) for ws in lines.values())
        return words, full, prepared.size

    def read(self, image: Image.Image, raw: bytes) -> dict[str, FieldReading]:
        words, text, (w, h) = self.words(image)
        out = {}
        for name, value in templates.parse_text(text).items():
            hits = _locate(words, value)
            conf = (sum(x.conf for x in hits) / len(hits) / 100) if hits else 0.6
            out[name] = FieldReading(name, value, round(conf, 3), _region(hits, w, h))
        return out


def _tokens(value: str) -> list[str]:
    return [t for t in re.split(r"\s+", value.strip()) if t]


def _locate(words: list[Word], value: str) -> list[Word]:
    wanted = {re.sub(r"[^A-Za-z0-9]", "", t).lower() for t in _tokens(value)}
    wanted.discard("")
    return [w for w in words if re.sub(r"[^A-Za-z0-9]", "", w.text).lower() in wanted]


def _region(hits: list[Word], w: int, h: int) -> dict[str, float] | None:
    if not hits:
        return None
    x0 = min(x.box[0] for x in hits)
    y0 = min(x.box[1] for x in hits)
    x1 = max(x.box[0] + x.box[2] for x in hits)
    y1 = max(x.box[1] + x.box[3] for x in hits)
    return {"page": 0, "x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h}
