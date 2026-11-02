"""Near-miss reference hints for reviewers. Advisory only: this never opens a gate.

A reference that is one character off a bank record (an O read as a 0, an I as a 1) is a likely
misreading, and a reviewer should see that candidate flagged. But the reference gate stays an
exact match, so a near miss can never be confirmed as it stands: the reviewer corrects the
extracted transaction id (an audited action) and the exact match then passes like any other.
"""

from typing import Any

# characters that OCR and eyes confuse; used only to label a hint as a likely lookalike error
_LOOKALIKE = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8"})

MAX_DISTANCE = 2
MIN_LENGTH = 8  # short references collide by chance, so they never get a hint


def edit_distance(a: str, b: str, cutoff: int = MAX_DISTANCE) -> int:
    """Levenshtein distance, giving up (returning cutoff + 1) once it must exceed `cutoff`."""
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > cutoff:
            return cutoff + 1
        previous = current
    return previous[-1]


def near_reference(claimed: str | None, recorded: str | None) -> dict[str, Any] | None:
    """Describe how a claimed reference nearly matches a recorded one, or None.

    Both arguments are already normalised (upper case, letters and digits only).
    """
    if not claimed or not recorded or claimed == recorded:
        return None
    if min(len(claimed), len(recorded)) < MIN_LENGTH:
        return None
    distance = edit_distance(claimed, recorded)
    if claimed.translate(_LOOKALIKE) == recorded.translate(_LOOKALIKE):
        return {"kind": "lookalike_characters", "distance": distance}
    if distance <= MAX_DISTANCE:
        return {"kind": "typo", "distance": distance}
    return None
