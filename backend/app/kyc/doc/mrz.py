"""Machine-readable zone parsing (ICAO 9303 TD1, TD2 and TD3) with check-digit validation.

Pure and deterministic. A valid MRZ is the strongest document signal we have: every field it
carries is protected by a check digit, so a misread or an edited character is detected rather than
trusted. OCR confusions between letters and digits are repaired only where the field is numeric,
and the result still has to pass its check digit.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

_WEIGHTS = (7, 3, 1)
_TO_DIGIT = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
)


def check_digit(data: str) -> int:
    """ICAO 9303 check digit: digits as-is, A-Z = 10..35, filler '<' = 0; weights 7,3,1."""
    total = 0
    for i, ch in enumerate(data):
        if ch.isdigit():
            v = int(ch)
        elif "A" <= ch <= "Z":
            v = ord(ch) - 55
        else:
            v = 0
        total += v * _WEIGHTS[i % 3]
    return total % 10


@dataclass
class MrzResult:
    format: str  # TD1 | TD2 | TD3
    doc_code: str  # P = passport, I/A/C = ID card
    issuing_country: str
    surname: str
    given_names: str
    document_number: str
    nationality: str
    date_of_birth: str | None  # ISO date
    sex: str | None
    expiry_date: str | None  # ISO date
    checks: dict[str, bool] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """Every check digit passes and the dates are real."""
        return (
            bool(self.checks)
            and all(self.checks.values())
            and self.date_of_birth is not None
            and self.expiry_date is not None
        )

    @property
    def doc_type(self) -> str:
        return "passport" if self.doc_code.startswith("P") else "national_id"

    @property
    def full_name(self) -> str:
        return f"{self.given_names} {self.surname}".strip()


def clean_line(line: str) -> str:
    out = re.sub(r"\s+", "", line.upper()).replace("«", "<").replace("‹", "<")
    return re.sub(r"[^A-Z0-9<]", "<", out)


def find_mrz(text: str) -> list[str]:
    """The MRZ lines inside OCR output: a block of 3x30 (TD1), 2x36 (TD2) or 2x44 (TD3).

    OCR commonly drops trailing `<` filler. The name line is padded with filler on the right, so
    a short name line is restored losslessly; the data lines carry check digits at fixed
    positions, so they must be the exact length (give or take a character, which the check
    digits then confirm or refuse)."""
    lines = [clean_line(ln) for ln in text.splitlines()]
    lines = [ln for ln in lines if re.fullmatch(r"[A-Z0-9<]{20,46}", ln) and "<" in ln]
    # (line length, number of lines, index of the name line)
    for size, count, name_at in ((44, 2, 0), (36, 2, 0), (30, 3, 2)):
        for start in range(len(lines) - count + 1):
            window = lines[start : start + count]
            fitted: list[str] = []
            for i, ln in enumerate(window):
                lo = 20 if i == name_at else size - 2
                if not lo <= len(ln) <= size + 2:
                    break
                fitted.append(ln.ljust(size, "<")[:size])
            else:
                return fitted
    return []


def _digits(s: str) -> str:
    return s.translate(_TO_DIGIT)


def _date(yymmdd: str, *, future: bool) -> str | None:
    s = _digits(yymmdd)
    if len(s) != 6 or not s.isdigit():
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    now_yy = datetime.now(UTC).year % 100
    if future:  # expiry dates: this century
        year = 2000 + yy
    else:  # birth dates: this century only if it is not in the future
        year = 2000 + yy if yy <= now_yy else 1900 + yy
    try:
        return date(year, mm, dd).isoformat()
    except ValueError:
        return None


def _names(block: str) -> tuple[str, str]:
    surname, _, given = block.strip("<").partition("<<")
    return surname.replace("<", " ").strip(), given.replace("<", " ").strip()


def _num_ok(value: str, digit: str) -> bool:
    """Check digit over a field that may be alphanumeric (document numbers) as read."""
    d = _digits(digit)
    return d.isdigit() and check_digit(value) == int(d)


def _date_ok(value: str, digit: str) -> bool:
    """Check digit over a numeric-only field: letters read in place of digits are repaired."""
    d = _digits(digit)
    return d.isdigit() and check_digit(_digits(value)) == int(d)


def parse(lines: list[str]) -> MrzResult | None:
    if len(lines) == 2 and len(lines[0]) == 44 and len(lines[1]) == 44:
        return _td3(lines)
    if len(lines) == 2 and len(lines[0]) == 36 and len(lines[1]) == 36:
        return _td2(lines)
    if len(lines) == 3 and all(len(ln) == 30 for ln in lines):
        return _td1(lines)
    return None


def _td3(lines: list[str]) -> MrzResult:
    l1, l2 = lines
    surname, given = _names(l1[5:44])
    composite = l2[0:10] + l2[13:20] + l2[21:43]
    checks = {
        "document_number": _num_ok(l2[0:9], l2[9]),
        "date_of_birth": _date_ok(l2[13:19], l2[19]),
        "expiry_date": _date_ok(l2[21:27], l2[27]),
        "composite": _num_ok(composite, l2[43]),
    }
    # the personal number check may legitimately be '<' when the field is empty
    if l2[28:42].strip("<"):
        checks["personal_number"] = _num_ok(l2[28:42], l2[42])
    return MrzResult(
        "TD3",
        l1[0:2].strip("<"),
        l1[2:5],
        surname,
        given,
        l2[0:9].strip("<"),
        l2[10:13],
        _date(l2[13:19], future=False),
        l2[20] if l2[20] in "MF" else None,
        _date(l2[21:27], future=True),
        checks,
        lines,
    )


def _td2(lines: list[str]) -> MrzResult:
    l1, l2 = lines
    surname, given = _names(l1[5:36])
    composite = l2[0:10] + l2[13:20] + l2[21:35]
    checks = {
        "document_number": _num_ok(l2[0:9], l2[9]),
        "date_of_birth": _date_ok(l2[13:19], l2[19]),
        "expiry_date": _date_ok(l2[21:27], l2[27]),
        "composite": _num_ok(composite, l2[35]),
    }
    return MrzResult(
        "TD2",
        l1[0:2].strip("<"),
        l1[2:5],
        surname,
        given,
        l2[0:9].strip("<"),
        l2[10:13],
        _date(l2[13:19], future=False),
        l2[20] if l2[20] in "MF" else None,
        _date(l2[21:27], future=True),
        checks,
        lines,
    )


def _td1(lines: list[str]) -> MrzResult:
    l1, l2, l3 = lines
    surname, given = _names(l3)
    composite = l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    checks = {
        "document_number": _num_ok(l1[5:14], l1[14]),
        "date_of_birth": _date_ok(l2[0:6], l2[6]),
        "expiry_date": _date_ok(l2[8:14], l2[14]),
        "composite": _num_ok(composite, l2[29]),
    }
    return MrzResult(
        "TD1",
        l1[0:2].strip("<"),
        l1[2:5],
        surname,
        given,
        l1[5:14].strip("<"),
        l2[15:18],
        _date(l2[0:6], future=False),
        l2[7] if l2[7] in "MF" else None,
        _date(l2[8:14], future=True),
        checks,
        lines,
    )


def read(text: str) -> MrzResult | None:
    """Find and parse an MRZ in OCR text; None when there is no well-formed block."""
    lines = find_mrz(text)
    return parse(lines) if lines else None


def compose_td3(
    *,
    doc_code: str = "P",
    country: str,
    surname: str,
    given: str,
    number: str,
    nationality: str,
    dob: date,
    sex: str,
    expiry: date,
) -> list[str]:
    """Build a valid passport MRZ (test fixtures and demos)."""
    name = f"{surname.upper()}<<{given.upper().replace(' ', '<')}"
    l1 = f"{doc_code:<<2}{country}{name}".ljust(44, "<")[:44]
    num = number.upper().ljust(9, "<")[:9]
    dob_s, exp_s = dob.strftime("%y%m%d"), expiry.strftime("%y%m%d")
    body = (
        f"{num}{check_digit(num)}{nationality}{dob_s}{check_digit(dob_s)}{sex}"
        f"{exp_s}{check_digit(exp_s)}{'<' * 14}0"
    )
    composite = body[0:10] + body[13:20] + body[21:43]
    return [l1, body + str(check_digit(composite))]
