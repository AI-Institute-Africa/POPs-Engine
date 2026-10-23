"""Prepare the synthetic POP samples as deterministic image-to-JSON JSONL splits."""

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path


DOCUMENT_TYPES = {
    "screenshots": "bank_transfer",
    "sms": "sms_notice",
    "complex": "bank_transfer",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "sample_data" / "mock_pops",
        help="Dataset root containing manifest.csv (defaults to sample_data/mock_pops)",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    root = args.dataset.resolve()
    with (root / "manifest.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise SystemExit(f"No records found in {root / 'manifest.csv'}")

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        category = row["category"]
        if category not in DOCUMENT_TYPES:
            raise SystemExit(f"Unsupported category {category!r} for record {row['id']}")
        for key in ("image_file", "text_file"):
            if not (root / row[key]).is_file():
                raise SystemExit(f"Missing {key} for {row['id']}: {root / row[key]}")
        grouped.setdefault(category, []).append(row)

    rng = random.Random(args.seed)
    splits: dict[str, list[dict[str, object]]] = {"train": [], "validation": [], "test": []}
    for category in sorted(grouped):
        category_rows = sorted(grouped[category], key=lambda item: item["id"])
        rng.shuffle(category_rows)
        train_end = int(len(category_rows) * 0.8)
        validation_end = train_end + int(len(category_rows) * 0.1)
        partitions = {
            "train": category_rows[:train_end],
            "validation": category_rows[train_end:validation_end],
            "test": category_rows[validation_end:],
        }
        for split, partition in partitions.items():
            for row in partition:
                labels = {
                    "provider": row["bank"] or None,
                    "document_type": DOCUMENT_TYPES[category],
                    "transaction_id": row["reference"] or None,
                    "amount": row["amount"] or None,
                    "currency": row["currency"] or None,
                    "date": row["date_time"] or None,
                    "sender": row.get("sender") or None,
                    "beneficiary": row.get("beneficiary") or None,
                    "status": row["status"] or None,
                    "narrative": None,
                    "tampering_notes": None,
                }
                splits[split].append({
                    "id": row["id"],
                    "category": category,
                    "image": row["image_file"],
                    "reference_text": row["text_file"],
                    "target": labels,
                })

    output = root / "training_splits"
    output.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict[str, int]] = {}
    for split, records in splits.items():
        records.sort(key=lambda item: str(item["id"]))
        with (output / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        summary[split] = dict(sorted(Counter(str(record["category"]) for record in records).items()))

    (output / "README.txt").write_text(
        "Synthetic POP image-to-JSON training splits.\n"
        "Each JSONL row has an image path, reference text path, and target using\n"
        "backend/app/extraction/schema.py ReceiptFields keys. Paths are relative to\n"
        "the dataset root (the parent of this folder).\n"
        f"Split is deterministic with seed {args.seed} and stratified by category.\n"
        "These generated, visibly watermarked samples are for pipeline practice only.\n"
        "They do not represent real bank layouts or establish real-world accuracy.\n"
        "For fine-tuning or evaluation, add reviewer-verified, consented examples and\n"
        "keep a separate held-out set of real receipts.\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} records to {output}")
    for split, counts in summary.items():
        print(f"{split}: {sum(counts.values())} {counts}")


if __name__ == "__main__":
    main()
