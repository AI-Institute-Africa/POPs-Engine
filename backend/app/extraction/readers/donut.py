"""Local Donut reader for an exported image-to-JSON checkpoint."""

import json
import logging
import math
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.schema import FIELD_NAMES, FieldReading, ReceiptFields

log = logging.getLogger(__name__)
_FLOOR, _CEIL = 0.01, 0.99
_JSON = json.JSONDecoder()


@lru_cache(maxsize=2)
def _load_model(model_path: str):
    path = Path(model_path).expanduser().resolve()
    if not (path / "config.json").is_file():
        raise ReaderUnavailable(f"Donut model config not found: {path}")
    try:
        import torch
        from transformers import DonutProcessor, VisionEncoderDecoderModel

        processor = DonutProcessor.from_pretrained(path, local_files_only=True)
        model = VisionEncoderDecoderModel.from_pretrained(path, local_files_only=True)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)  # type: ignore[arg-type,unused-ignore]
        model.eval()
        log.warning(
            "Donut reader loaded from %s: fine-tuned on synthetic receipts only; benchmark it on "
            "real receipts and keep shadow mode on before relying on it",
            path,
        )
        return processor, model, device, torch
    except Exception as exc:
        raise ReaderUnavailable(f"could not load Donut model at {path}: {exc}") from exc


def _value_span(decoded: str, name: str) -> tuple[int, int] | None:
    """Character span of the value in `"name": <value>` within the generated JSON text."""
    i = decoded.find(f'"{name}"')
    if i < 0:
        return None
    i += len(name) + 2
    while i < len(decoded) and decoded[i].isspace():
        i += 1
    if i >= len(decoded) or decoded[i] != ":":
        return None
    i += 1
    while i < len(decoded) and decoded[i].isspace():
        i += 1
    try:
        value, end = _JSON.raw_decode(decoded, i)
    except ValueError:
        return None
    if isinstance(value, str):
        return i + 1, end - 1  # the characters of the value, not its JSON quotes
    return i, end


def token_spans(decode: Callable[[list[int]], str], ids: Sequence[int]) -> list[tuple[int, int]]:
    """Character span each generated token adds to the decoded text (empty for special tokens)."""
    spans: list[tuple[int, int]] = []
    prev = 0
    for i in range(len(ids)):
        # a re-decode of a longer prefix can occasionally shorten the text; keep offsets monotonic
        end = max(prev, len(decode(list(ids[: i + 1]))))
        spans.append((prev, end))
        prev = end
    return spans


def field_confidences(
    decoded: str, spans: Sequence[tuple[int, int]], probs: Sequence[float]
) -> dict[str, float]:
    """Per-field confidence: geometric mean of the model's probability for each token of the value.

    These are the model's own token probabilities, so they rank fields from shaky to firm but are
    not calibrated (an under-trained model is over-confident); measure them with the benchmark
    before trusting a threshold.
    """
    out: dict[str, float] = {}
    for name in FIELD_NAMES:
        span = _value_span(decoded, name)
        if span is None:
            continue
        start, end = span
        hit = [p for (s, e), p in zip(spans, probs, strict=False) if s < end and e > start]
        if not hit:
            continue
        mean_log = sum(math.log(max(p, 1e-9)) for p in hit) / len(hit)
        out[name] = min(_CEIL, max(_FLOOR, math.exp(mean_log)))
    return out


class DonutReader:
    """Read one receipt image with the locally fine-tuned Donut model."""

    name = "donut_vlm"
    per_page = True
    # Used only for a field whose tokens cannot be located in the generated text.
    confidence = 0.5

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or get_settings().donut_model_path

    @property
    def version(self) -> str:
        return f"donut:{Path(self.model_path).name}"

    def read(self, image: Image.Image, raw: bytes) -> dict[str, FieldReading]:
        processor, model, device, torch = _load_model(self.model_path)
        try:
            pixels = processor(image.convert("RGB"), return_tensors="pt").pixel_values.to(device)
            with torch.inference_mode():
                generated = model.generate(
                    pixels,
                    max_new_tokens=256,
                    num_beams=1,
                    do_sample=False,
                    pad_token_id=processor.tokenizer.pad_token_id,
                    eos_token_id=processor.tokenizer.eos_token_id,
                    return_dict_in_generate=True,
                    output_scores=True,
                )
                transition = model.compute_transition_scores(
                    generated.sequences, generated.scores, normalize_logits=True
                )
            logprobs = [float(x) for x in transition[0].tolist()]
            sequence = [int(x) for x in generated.sequences[0].tolist()]
            ids = sequence[len(sequence) - len(logprobs) :]  # drop the decoder prompt

            def decode(token_ids: list[int]) -> str:
                return processor.batch_decode([token_ids], skip_special_tokens=True)[0]

            text = decode(ids)
            probs = [math.exp(lp) for lp in logprobs]
            confidences = field_confidences(text, token_spans(decode, ids), probs)

            decoded = text.strip()
            start, end = decoded.find("{"), decoded.rfind("}")
            if start < 0 or end < start:
                raise ValueError("model output did not contain a JSON object")
            parsed = ReceiptFields.model_validate(json.loads(decoded[start : end + 1]))
        except Exception as exc:
            raise ReaderUnavailable(f"Donut inference failed: {exc}") from exc

        values = parsed.model_dump()
        return {
            name: FieldReading(
                name=name, raw=values[name], confidence=confidences.get(name, self.confidence)
            )
            for name in FIELD_NAMES
            if values.get(name) is not None
        }
