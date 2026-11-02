"""Read one SYNTHETIC receipt with the real Ollama reader. Opt-in; not part of CI.

    POPS_OLLAMA_API_KEY=... PYTHONPATH=. python scripts/live_smoke.py

It sends a generated test image (no real payer data) to whatever POPS_OLLAMA_URL points at, so it
costs a request on that account. Use it to confirm the key, URL and model work and that the
reader returns the fields the engine expects. Never point it at real receipts: use
scripts/benchmark_reader.py on approved infrastructure for that.
"""

import io
import sys

from PIL import Image

from app.core.config import get_settings
from app.devtools import dmy, ecocash_fields, make_receipt
from app.extraction.readers.ollama_vlm import OllamaVLMReader

EXPECTED = {"amount": "40.00", "currency": "USD", "provider": "EcoCash"}


def main() -> int:
    s = get_settings()
    if s.ollama_url.startswith("https://") and not s.ollama_api_key:
        print("POPS_OLLAMA_API_KEY is not set; nothing was sent.")
        return 2
    fields = ecocash_fields(date=dmy("14:32"))
    png = make_receipt(
        fields,
        lines=[
            "EcoCash",
            "Payment Successful",
            f"Txn ID: {fields['transaction_id']}",
            "Amount: USD 40.00",
            "Paid to: COGNIFY MAIN",
            fields["date"],
        ],
    )
    reader = OllamaVLMReader()
    print(f"reader: {reader.version} at {reader.url}")
    out = reader.read(Image.open(io.BytesIO(png)), png)
    for name, reading in sorted(out.items()):
        print(f"  {name:15s} {reading.raw!r}  (confidence {reading.confidence:.2f})")
    missing = [
        k for k, v in EXPECTED.items() if k not in out or v.lower() not in str(out[k].raw).lower()
    ]
    if missing:
        print(f"UNEXPECTED: {', '.join(missing)} not read as {EXPECTED}")
        return 1
    print("live read ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
