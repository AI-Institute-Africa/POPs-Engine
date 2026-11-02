"""Donut reader: per-field confidence from token probabilities, and the failure path."""

import contextlib
import io
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.extraction.readers import donut
from app.extraction.readers.base import ReaderUnavailable

PROMPT, EOS = 98, 99
PIECES = ['{"', "amount", '": "', "40", ".", "00", '", "', "status", '": "', "Successful", '"}']
TEXT = "".join(PIECES)  # {"amount": "40.00", "status": "Successful"}
#                amount value tokens ---^^^^^^^^^^^^^^^^   status value token ---^^^^^^^^^^^
PROBS = [0.99, 0.99, 0.99, 0.95, 0.95, 0.9, 0.99, 0.99, 0.99, 0.2, 0.99, 0.99]  # last = EOS
IMAGE = Image.new("RGB", (8, 8))


class _Seq(list):
    def tolist(self):
        return list(self)


class _Processor:
    """Decodes by joining pieces; ids at or above len(pieces) are special tokens (skipped)."""

    tokenizer = SimpleNamespace(pad_token_id=0, eos_token_id=EOS)

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces

    def __call__(self, image, return_tensors):
        return SimpleNamespace(pixel_values=SimpleNamespace(to=lambda device: object()))

    def batch_decode(self, batch, skip_special_tokens):
        return ["".join(self.pieces[i] for i in ids if i < len(self.pieces)) for ids in batch]


def _decode(ids: list[int]) -> str:
    return _Processor(PIECES).batch_decode([ids], True)[0]


def _stub(monkeypatch, pieces: list[str] = PIECES, probs: list[float] = PROBS) -> None:
    """Make the reader 'generate' `pieces` with the given token probabilities."""
    ids = list(range(len(pieces)))
    model = SimpleNamespace(
        generate=lambda *a, **k: SimpleNamespace(sequences=[_Seq([PROMPT, *ids, EOS])], scores=()),
        compute_transition_scores=lambda *a, **k: [_Seq(math.log(p) for p in probs)],
    )
    torch = SimpleNamespace(inference_mode=contextlib.nullcontext)
    monkeypatch.setattr(
        donut, "_load_model", lambda path: (_Processor(pieces), model, "cpu", torch)
    )


def test_value_span_finds_each_value():
    assert TEXT[slice(*donut._value_span(TEXT, "amount"))] == "40.00"
    assert TEXT[slice(*donut._value_span(TEXT, "status"))] == "Successful"
    assert donut._value_span('{"n": 5}', "n") == (6, 7)  # non-string values keep their span
    assert donut._value_span(TEXT, "currency") is None
    assert donut._value_span('{"amount": ', "amount") is None  # value never finished


def test_token_spans_cover_the_text_and_special_tokens_are_empty():
    spans = donut.token_spans(_decode, [*range(len(PIECES)), EOS])
    assert spans[0] == (0, 2) and spans[-1] == (len(TEXT), len(TEXT))
    assert "".join(TEXT[s:e] for s, e in spans) == TEXT


def test_confidence_is_per_field_and_reflects_the_weakest_tokens():
    spans = donut.token_spans(_decode, [*range(len(PIECES)), EOS])
    conf = donut.field_confidences(TEXT, spans, PROBS)
    # the amount tokens are firm, the status value is a guess; structure tokens do not count
    assert conf["amount"] > 0.9
    assert conf["status"] == pytest.approx(0.2, abs=0.05)
    assert "currency" not in conf


def test_confidence_is_clamped():
    whole = [(0, len(TEXT))]
    assert donut.field_confidences(TEXT, whole, [1.0])["amount"] == 0.99
    assert donut.field_confidences(TEXT, whole, [0.0])["amount"] == 0.01


def test_reader_reports_per_field_confidence(monkeypatch):
    _stub(monkeypatch)
    out = donut.DonutReader(model_path="unused").read(IMAGE, b"")
    assert {n: f.raw for n, f in out.items()} == {"amount": "40.00", "status": "Successful"}
    assert out["amount"].confidence > 0.9
    assert out["status"].confidence < 0.35


def test_reader_fails_closed_on_unparseable_output(monkeypatch):
    _stub(monkeypatch, pieces=["no json here"] * len(PIECES))
    with pytest.raises(ReaderUnavailable, match="Donut inference failed"):
        donut.DonutReader(model_path="unused").read(IMAGE, b"")


def test_missing_checkpoint_is_reported_not_crashed(tmp_path):
    with pytest.raises(ReaderUnavailable, match="config not found"):
        donut._load_model(str(tmp_path / "nope"))


MODEL = Path(os.environ.get("POPS_DONUT_MODEL_PATH", "../var/models/donut-pops"))


@pytest.mark.skipif(not (MODEL / "config.json").is_file(), reason="no local Donut checkpoint")
def test_real_checkpoint_reads_a_synthetic_receipt():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except (ImportError, OSError) as exc:  # OSError: a policy can block torch's DLLs
        pytest.skip(f"torch/transformers unavailable: {exc}")
    from app.devtools import ecocash_fields, make_receipt

    img = Image.open(io.BytesIO(make_receipt(ecocash_fields(), size=(640, 960))))
    out = donut.DonutReader(model_path=str(MODEL)).read(img, b"")
    assert out, "the model returned no fields"
    assert all(0.0 < f.confidence < 1.0 for f in out.values())
    # a single constant across fields would mean the token scores are not wired through
    assert len({round(f.confidence, 3) for f in out.values()}) > 1
