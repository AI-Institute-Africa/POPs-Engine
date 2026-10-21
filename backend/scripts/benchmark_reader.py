"""Phase 0 reader benchmark: score one or more Ollama vision models on labelled local POPs.

Layout of the labelled set (never used for training; see blueprint 12.2):
    <dir>/labels.jsonl   one JSON object per line: {"file": "x.png", "amount": "40.00",
                         "currency": "USD", "transaction_id": "...", "date": "...", ...}
    <dir>/<files>        the POP images or PDFs

    python scripts/benchmark_reader.py <dir> qwen3-vl:235b gemma3:27b qwen2.5vl:7b

Scores are per field, exact match after the engine's own normalisation, plus latency.
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from app.extraction.agreement import normalise_field
from app.extraction.readers.base import ReaderUnavailable
from app.extraction.readers.ollama_vlm import OllamaVLMReader
from app.intake.files import prepare

CRITICAL = ("amount", "transaction_id", "date", "currency", "beneficiary", "status")


def main(folder: str, models: list[str]) -> None:
    root = Path(folder)
    labels = [json.loads(line) for line in (root / "labels.jsonl").read_text().splitlines() if line]
    for model in models:
        reader = OllamaVLMReader(model=model)
        hits: dict[str, int] = defaultdict(int)
        seen: dict[str, int] = defaultdict(int)
        failures, latency = 0, []
        for item in labels:
            raw = (root / item["file"]).read_bytes()
            prepared = prepare(raw, max_bytes=len(raw) + 1)
            start = time.monotonic()
            try:
                got = reader.read(prepared.image, raw)
            except ReaderUnavailable:
                failures += 1
                continue
            latency.append(time.monotonic() - start)
            currency = normalise_field("currency", item.get("currency"), None)
            for name in CRITICAL:
                if name not in item:
                    continue
                seen[name] += 1
                want = normalise_field(name, item[name], currency)
                have = got.get(name)
                if have and normalise_field(name, have.raw, currency) == want:
                    hits[name] += 1
        print(f"\n== {model}  ({len(labels)} docs, {failures} failures)")
        for name in CRITICAL:
            if seen[name]:
                print(f"  {name:15s} {hits[name] / seen[name]:6.1%}  ({hits[name]}/{seen[name]})")
        if latency:
            latency.sort()
            print(
                f"  latency p50 {latency[len(latency) // 2]:.1f}s  p95 "
                f"{latency[int(len(latency) * 0.95) - 1]:.1f}s"
            )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
