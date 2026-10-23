"""Local Donut reader for an exported image-to-JSON checkpoint."""

import json
from functools import lru_cache
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.schema import FIELD_NAMES, FieldReading, ReceiptFields


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
        model.to(device)
        model.eval()
        return processor, model, device, torch
    except Exception as exc:
        raise ReaderUnavailable(f"could not load Donut model at {path}: {exc}") from exc


class DonutReader:
    """Read one receipt image with the locally fine-tuned Donut model."""

    name = "donut_vlm"
    per_page = True
    confidence = 0.75  # Donut generation scores are not calibrated probabilities.

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
                )
            decoded = processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
            start, end = decoded.find("{"), decoded.rfind("}")
            if start < 0 or end < start:
                raise ValueError("model output did not contain a JSON object")
            parsed = ReceiptFields.model_validate(json.loads(decoded[start : end + 1]))
        except Exception as exc:
            raise ReaderUnavailable(f"Donut inference failed: {exc}") from exc

        values = parsed.model_dump()
        return {
            name: FieldReading(name=name, raw=values[name], confidence=self.confidence)
            for name in FIELD_NAMES
            if values.get(name) is not None
        }
