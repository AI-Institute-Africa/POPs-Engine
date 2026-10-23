"""Create sample obligations for the local POPs Engine live demo.

Uses only Python's standard library so it can run outside the backend container.
It never creates claims or credits; upload the listed synthetic proofs from the console.
"""

import csv
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("POPS_DEMO_API", "http://localhost:8000").rstrip("/")
MANIFEST = ROOT / "sample_data" / "mock_pops" / "manifest.csv"
CATEGORIES = ("screenshots", "sms", "complex")


def request(path: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach {API}. Start the Docker Compose demo stack first and confirm "
            "the API is available at http://localhost:8000."
        ) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{path}: HTTP {exc.code}: {detail}") from exc


def main() -> None:
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    samples = []
    for category in CATEGORIES:
        category_rows = [r for r in rows if r["category"] == category]
        candidates = [r for r in category_rows if r["status"] == "SUCCESSFUL"]
        if not candidates:
            # SMS samples intentionally model varied real-world message states. Any sample is
            # useful for demonstrating extraction; it remains unconfirmed evidence either way.
            candidates = category_rows
        if not candidates:
            raise RuntimeError(f"No sample found in category {category}")
        sample = next((r for r in candidates if r["currency"] == "USD"), candidates[0])
        sample["image_path"] = str((MANIFEST.parent / sample["image_file"]).resolve())
        samples.append(sample)

    session = request("/auth/dev-login", body={
        "email": "demo@local.dev", "role": "submitter", "org_name": "Cognify"
    })
    token = session["access_token"]
    existing = request("/obligations?limit=500", token=token)
    by_ref = {item.get("external_ref"): item for item in existing if item.get("external_ref")}

    print("Demo payment codes and synthetic proof files")
    print("Upload these files in the console at http://localhost:5173")
    for i, sample in enumerate(samples, start=1):
        code = f"LIVE-DEMO-{sample['category'].upper()}"
        obligation = by_ref.get(code)
        if obligation is None:
            amount_minor = int(Decimal(sample["amount"]) * 100)
            obligation = request("/obligations", token=token, body={
                "learner_ref": f"DEMO-{i:03d}",
                "type": "instalment",
                "amount_minor": amount_minor,
                "currency": sample["currency"],
                "description": f"Synthetic live demo POP ({sample['category']})",
                "external_ref": code,
            })
        print(f"\n{sample['category'].upper()}  code={code}")
        print(
            f"  amount={sample['currency']} {sample['amount']}  status={sample['status']}"
            f"  reference={sample['reference']}"
        )
        print(f"  proof={sample['image_path']}")
    print("\nSeed complete. No claims or credits were created.")


if __name__ == "__main__":
    main()
