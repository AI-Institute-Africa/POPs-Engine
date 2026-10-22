"""Export confirmed payments as a labelled set for scripts/benchmark_reader.py (Phase 0).

A claim whose money was allocated from a bank, alert or gateway record has a known truth: the
credit says what the receipt *should* have read. This writes those receipts and that truth to a
folder in the layout benchmark_reader.py expects:

    python scripts/export_labelled_set.py <out-dir> [--org <id>] [--since-days 180] [--limit 500]
    python scripts/benchmark_reader.py <out-dir> qwen3-vl:235b gemma3:27b

Only single-file submissions are exported (with several files we cannot tell which one shows
the reference), and only credits with a real provider reference (not synthetic statement keys,
not manual credits). The date is not labelled: the bank's posting time is not the receipt's.

The folder holds payers' personal data. Keep it on infrastructure covered by your data
protection approvals, never in the repository, and delete it when the benchmark is done.
"""

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.db import session_scope
from app.core.money import CURRENCY_EXPONENT
from app.intake.service import submission_evidence
from app.intake.storage import get_storage
from app.models import Allocation, Claim, Credit

TRUSTED_SOURCES = ("statement", "alert", "gateway")


def _amount(minor: int, currency: str) -> str:
    exp = CURRENCY_EXPONENT.get(currency, 2)
    return f"{Decimal(minor) / (Decimal(10) ** exp):.{exp}f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--org", type=uuid.UUID)
    ap.add_argument("--since-days", type=int, default=180)
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    since = datetime.now(UTC) - timedelta(days=args.since_days)
    storage = get_storage()
    written = skipped = 0
    with session_scope() as session, (out / "labels.jsonl").open("w") as labels:
        q = (
            select(Claim, Credit)
            .join(Allocation, Allocation.claim_id == Claim.claim_id)
            .join(Credit, Credit.credit_id == Allocation.credit_id)
            .where(
                Allocation.kind == "allocation",
                Credit.source.in_(TRUSTED_SOURCES),
                Claim.created_at >= since,
            )
            .order_by(Claim.created_at.desc())
            .limit(args.limit)
        )
        if args.org:
            q = q.where(Claim.org_id == args.org)
        seen: set[uuid.UUID] = set()
        for claim, credit in session.execute(q).all():
            if claim.claim_id in seen:
                continue
            seen.add(claim.claim_id)
            evs = submission_evidence(session, claim.claim_id)
            ref = credit.provider_txn_id
            if len(evs) != 1 or ref.startswith("SYN") or "#" in ref:
                skipped += 1
                continue
            ev = evs[0]
            name = f"{claim.claim_id}.{ev.object_key.rsplit('.', 1)[-1]}"
            (out / name).write_bytes(storage.get(ev.object_key))
            item = {
                "file": name,
                "amount": _amount(credit.amount_minor, credit.currency),
                "currency": credit.currency,
                "transaction_id": credit.provider_txn_id,
                "channel": claim.channel,
                "provider": credit.provider,
                "content_type": ev.content_type,
            }
            labels.write(json.dumps(item) + "\n")
            written += 1
    print(f"wrote {written} labelled receipts to {out} ({skipped} skipped)")


if __name__ == "__main__":
    main()
