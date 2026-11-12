"""Image-to-text transcription (OCR) for the console's converter.

This is deliberately separate from the claim pipeline. The pipeline asks two independent
readers for named payment fields and cross-checks them before anything can move money; the
converter only answers "what text is in this file?" and its output is never used in a decision.

Digital text layers are returned exactly. Images are read with Tesseract OCR, with the
configured vision model as a fallback when OCR cannot read the page (and only when the
organisation's guardrails allow the vision model to run).
"""

from dataclasses import dataclass, field

from app.core.config import get_settings
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.readers.fake import fixture_text
from app.intake.files import PreparedFile

TEXT_LAYER = "text_layer"
TEXT_LAYER_CONFIDENCE = 0.99
FIXTURE_TEXT = "fixture_text"
FIXTURE_TEXT_CONFIDENCE = 0.99


class TranscriptionUnavailable(RuntimeError):
    """No reader could turn the file into text."""


@dataclass
class PageText:
    page: int  # 1-based
    text: str
    reader: str
    confidence: float | None = None

    @property
    def lines(self) -> int:
        return len([line for line in self.text.splitlines() if line.strip()])


@dataclass
class Transcription:
    pages: list[PageText]
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text).strip()


def _tesseract():
    from app.extraction.readers.tesseract_ocr import TesseractReader

    return TesseractReader()


def _vision_reader():
    s = get_settings()
    if s.vlm_backend == "ollama":
        from app.extraction.readers.ollama_vlm import OllamaVLMReader

        return OllamaVLMReader()
    return None  # the Donut reader returns named fields, not a transcription


def transcribe(
    prepared: PreparedFile, raw: bytes, *, vlm_allowed: bool = True
) -> Transcription:
    """Return the text of every page, in order. Never writes anything."""
    s = get_settings()
    pages: list[PageText] = []
    failures: dict[str, str] = {}
    tesseract = None
    tesseract_checked = False
    vision = None
    vision_checked = False

    for index, image in enumerate(prepared.pages):
        digital = prepared.page_texts[index].strip() if index < len(prepared.page_texts) else ""
        if digital:
            pages.append(PageText(index + 1, digital, TEXT_LAYER, TEXT_LAYER_CONFIDENCE))
            continue

        text, reader, confidence = "", "", None

        # In the fake-reader mode used by tests and demos, fixture text wins so the result is
        # deterministic and needs no OCR binary.
        if s.vlm_backend == "fake":
            fixture = fixture_text(raw)
            if fixture:
                text, reader, confidence = fixture, FIXTURE_TEXT, FIXTURE_TEXT_CONFIDENCE

        if not text:
            if not tesseract_checked:
                tesseract_checked = True
                try:
                    tesseract = _tesseract()
                except ReaderUnavailable as exc:
                    failures["tesseract_ocr"] = str(exc)
            if tesseract is not None:
                try:
                    text, confidence = tesseract.transcribe(image)
                except ReaderUnavailable as exc:
                    failures[tesseract.name] = str(exc)
                    tesseract = None
                if text.strip():
                    reader = tesseract.name if tesseract is not None else "tesseract_ocr"
                else:
                    text = ""

        if not text:
            if not vision_checked:
                vision_checked = True
                vision = _vision_reader()
                if vision is None and tesseract is None and vlm_allowed:
                    failures["vision"] = (
                        "OCR is not installed and the configured reader cannot transcribe "
                        "plain text"
                    )
            if vision is not None:
                try:
                    text, confidence = vision.transcribe(image)
                    if text.strip():
                        reader = vision.name
                    else:
                        text = ""
                except ReaderUnavailable as exc:
                    failures[vision.name] = str(exc)
                    vision = None

        pages.append(PageText(index + 1, text.strip(), reader, confidence))

    if failures and not any(page.text for page in pages):
        raise TranscriptionUnavailable(
            "Text conversion is not available on this server: "
            + "; ".join(dict.fromkeys(failures.values()))
        )
    return Transcription(pages, failures)
