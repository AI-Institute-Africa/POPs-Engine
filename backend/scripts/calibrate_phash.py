"""Choose the near-duplicate threshold from real data (policy: phash_duplicate_distance).

    python scripts/calibrate_phash.py <folder-of-distinct-genuine-POPs> [<folder-of-known-copies>]

The first folder holds receipts known to be *different* payments (ideally many from the same
provider, since same-template receipts look alike). The optional second folder holds re-saved,
cropped or screenshotted copies named like their original (e.g. a.png and a-copy.jpg).
It prints the distance distribution so you can pick a threshold below most distinct pairs and
above most copies.
"""

import sys
from itertools import combinations
from pathlib import Path

from app.intake.files import phash_distance, prepare


def hashes(folder: Path) -> dict[str, str]:
    out = {}
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}:
            data = p.read_bytes()
            out[p.stem] = prepare(data, max_bytes=len(data) + 1).phash
    return out


def summary(name: str, dists: list[int]) -> None:
    if not dists:
        return
    dists.sort()
    pct = {q: dists[min(len(dists) - 1, int(len(dists) * q))] for q in (0.01, 0.05, 0.5)}
    print(
        f"{name}: n={len(dists)} min={dists[0]} p1={pct[0.01]} p5={pct[0.05]} "
        f"median={pct[0.5]} max={dists[-1]}"
    )
    for t in (4, 6, 8, 10, 12):
        share = sum(d <= t for d in dists) / len(dists)
        print(f"   threshold {t:2d}: {share:6.1%} of pairs would be flagged")


distinct = hashes(Path(sys.argv[1]))
summary(
    "distinct pairs (want ~0% flagged)",
    [phash_distance(a, b) for a, b in combinations(distinct.values(), 2)],
)
if len(sys.argv) > 2:
    copies = hashes(Path(sys.argv[2]))
    pairs = [(c, o) for c in copies for o in distinct if c.startswith(o)]
    summary(
        "copy pairs (want ~100% flagged)",
        [phash_distance(copies[c], distinct[o]) for c, o in pairs],
    )
