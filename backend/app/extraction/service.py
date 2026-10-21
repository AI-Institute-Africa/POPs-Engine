"""Extraction orchestration: run every available reader, then combine (pipeline stage 3)."""

import logging
from dataclasses import dataclass, field

from PIL import Image

from app.core.config import get_settings
from app.extraction.agreement import FinalField, combine
from app.extraction.readers.base import Reader, ReaderUnavailable
from app.extraction.readers.fake import FakeOCRReader, FakeVLMReader
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


def extract(
    image: Image.Image,
    raw: bytes,
    critical: list[str],
    single_reader_min_conf: float,
    readers: list[Reader] | None = None,
) -> ExtractionResult:
    readers = readers if readers is not None else build_readers()
    readings: dict[str, tuple[str, dict[str, FieldReading]]] = {}
    failures: dict[str, str] = {}
    for reader in readers:
        try:
            readings[reader.name] = (reader.version, reader.read(image, raw))
        except ReaderUnavailable as exc:
            failures[reader.name] = str(exc)  # fail closed: fewer readers -> less agreement
    fields = combine(readings, critical, single_reader_min_conf) if readings else {}
    return ExtractionResult(
        fields=fields, versions={n: v for n, (v, _) in readings.items()}, failures=failures
    )
