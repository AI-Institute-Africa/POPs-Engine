"""Deterministic readers for tests and demos; no model or OCR binary needed.

Fixture PNGs carry their "ground truth" in tEXt chunks: `pops-vlm` for the primary reader and
`pops-ocr` for the second reader, each a JSON object of field -> value (optionally
{"value":..,"confidence":..,"region":{..}}). Different chunks let tests simulate disagreement.
"""

import io
import json

from PIL import Image

from app.extraction.schema import FieldReading


def _chunk(raw: bytes, key: str) -> dict:
    try:
        info = Image.open(io.BytesIO(raw)).info
    except Exception:  # noqa: BLE001
        return {}
    text = info.get(key)
    return json.loads(text) if text else {}


def _readings(data: dict, default_conf: float) -> dict[str, FieldReading]:
    out = {}
    for name, val in data.items():
        if isinstance(val, dict):
            out[name] = FieldReading(
                name, val.get("value"), val.get("confidence", default_conf), val.get("region")
            )
        elif val is not None:
            out[name] = FieldReading(name, str(val), default_conf)
    return out


class FakeVLMReader:
    name = "fake_vlm"
    version = "fake:1"

    def read(self, image: Image.Image, raw: bytes) -> dict[str, FieldReading]:
        return _readings(_chunk(raw, "pops-vlm"), 0.8)


class FakeOCRReader:
    name = "fake_ocr"
    version = "fake:1"

    def read(self, image: Image.Image, raw: bytes) -> dict[str, FieldReading]:
        data = _chunk(raw, "pops-ocr") or _chunk(raw, "pops-vlm")
        return _readings(data, 0.9)
