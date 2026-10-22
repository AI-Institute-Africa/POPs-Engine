"""Extraction orchestration: run every available reader, then combine (pipeline stage 3)."""

import logging
from dataclasses import dataclass, field

from PIL import Image

from app.core.config import get_settings
from app.extraction.agreement import FinalField, combine
from app.extraction.readers.base import Reader, ReaderUnavailable
from app.extraction.readers.fake import FakeOCRReader, FakeVLMReader
from app.extraction.readers.text_layer import TextLayerReader
from app.extraction.schema import FieldReading

log = logging.getLogger(__name__)


def build_readers(vlm_allowed: bool = True, hints: list[str] | None = None) -> list[Reader]:
    """Readers for one claim. Guardrails may switch the VLM off or add reading hints."""
    s = get_settings()
    readers: list[Reader] = []
    if vlm_allowed and s.vlm_backend == "fake":
        readers.append(FakeVLMReader())
    elif vlm_allowed:
        from app.extraction.readers.ollama_vlm import OllamaVLMReader

        readers.append(OllamaVLMReader(hints=hints))
    if s.vlm_backend == "fake":
        readers.append(FakeOCRReader())
    elif s.ocr_backend == "tesseract":
        try:
            from app.extraction.readers.tesseract_ocr import TesseractReader

            readers.append(TesseractReader())
        except ReaderUnavailable as exc:
            log.warning("second reader unavailable: %s", exc)
    return readers


@dataclass
class ExtractionResult:
    fields: dict[str, FinalField]
    versions: dict[str, str]
    failures: dict[str, str] = field(default_factory=dict)


def _merge_pages(
    per_page: list[dict[str, FieldReading]], layout: list[tuple[float, float]]
) -> dict[str, FieldReading]:
    """First page that yields each field wins; regions move into stacked-preview coordinates."""
    out: dict[str, FieldReading] = {}
    for page_no, readings in enumerate(per_page):
        y0, h = layout[page_no] if page_no < len(layout) else (0.0, 1.0)
        for name, r in readings.items():
            if name in out or not r.raw:
                continue
            region = None
            if r.region:
                region = {
                    **r.region,
                    "page": page_no,
                    "y": y0 + r.region["y"] * h,
                    "h": r.region["h"] * h,
                }
            out[name] = FieldReading(name, r.raw, r.confidence, region)
    return out


def extract(
    pages: list[Image.Image] | Image.Image,
    raw: bytes,
    critical: list[str],
    single_reader_min_conf: float,
    readers: list[Reader] | None = None,
    text: str = "",
    layout: list[tuple[float, float]] | None = None,
) -> ExtractionResult:
    """Run every reader over every page (and the digital text layer), then combine."""
    pages = pages if isinstance(pages, list) else [pages]
    layout = layout or [(0.0, 1.0)]
    readers = readers if readers is not None else build_readers()
    readings: dict[str, tuple[str, dict[str, FieldReading]]] = {}
    failures: dict[str, str] = {}
    for reader in readers:
        try:
            if getattr(reader, "per_page", False) and len(pages) > 1:
                result = _merge_pages([reader.read(p, raw) for p in pages], layout)
            else:
                result = reader.read(pages[0], raw)
            readings[reader.name] = (reader.version, result)
        except ReaderUnavailable as exc:
            failures[reader.name] = str(exc)  # fail closed: fewer readers -> less agreement
    if text.strip():
        tl = TextLayerReader(text)
        readings[tl.name] = (tl.version, tl.read_text())
    fields = combine(readings, critical, single_reader_min_conf) if readings else {}
    return ExtractionResult(
        fields=fields, versions={n: v for n, (v, _) in readings.items()}, failures=failures
    )
