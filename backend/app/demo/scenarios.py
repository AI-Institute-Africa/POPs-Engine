"""Demo scenarios: short stories a person can run with one click, or try by hand.

A scenario drives the real API as the real roles (a payer, a reviewer, ...), so what the viewer
sees afterwards in the console is exactly what the product produced. `outcome` and `reason` say
what the product is expected to do, and tests assert both, so the demo cannot quietly drift from
the engine. The same sample builders back the "try it yourself" buttons in the console.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.demo import art
from app.devtools import dmy, ecocash_fields, statement_csv

DEMO_ORG = "Demo Academy"
PERSONAS = {
    "payer": ("amara.payer@demo.validate", "submitter", "Payer (learner)"),
    "reviewer": ("rudo.reviewer@demo.validate", "reviewer", "Finance reviewer"),
    "approver": ("kuda.approver@demo.validate", "approver", "Finance approver"),
    "admin": ("tariro.admin@demo.validate", "admin", "Administrator"),
}

POLICY = {
    "auto_approve_enabled": True,
    "auto_approve_channels": ["ecocash"],
    "auto_approve_max_minor": {"USD": 20_000},
    "auto_approve_audit_pct": 20,
    "kyc_auto_verify_mode": "on",
    "kyc_allowed_countries": [art.COUNTRY],
    "kyc_min_face_score": 0.6,
    "kyc_audit_pct": 25,
}
POLICY_VERSION = "demo-v1"


class DemoError(RuntimeError):
    pass


class Api:
    """The product's API, called as one of the demo personas."""

    def __init__(self, http: httpx.Client, known_hashes: Callable[[], list[int]] | None = None):
        self.http = http
        self._tok: dict[str, dict[str, str]] = {}
        self._known = known_hashes or (lambda: [])

    def as_(self, who: str) -> dict[str, str]:
        if who not in self._tok:
            email, role, _ = PERSONAS[who]
            r = self.http.post(
                "/auth/dev-login", json={"email": email, "role": role, "org_name": DEMO_ORG}
            )
            r.raise_for_status()
            self._tok[who] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self._tok[who]

    def call(self, who: str, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, path, headers=self.as_(who), **kw)
        if r.status_code >= 400:
            raise DemoError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def receipt(self, fields: dict, **kw: Any) -> bytes:
        """A sample receipt that is not a near-duplicate of anything already submitted."""
        return art.distinct_receipt(fields, self._known(), **kw)


@dataclass
class Step:
    who: str  # a key of PERSONAS, or "system"
    text: str


@dataclass
class Result:
    scenario: str
    kind: str  # payment | kyc
    ref_id: str
    state: str
    headline: str
    steps: list[Step] = field(default_factory=list)
    followups: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    payer_messages: list[str] = field(default_factory=list)  # what the person is told


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str  # payments | identity
    title: str
    pitch: str  # one line on the card
    learn: str  # the point of it, for the result panel
    outcome: str  # expected final state (asserted in tests)
    reason: str  # a reason code that must be present (asserted in tests)
    run: Callable[[Api], Result]


# --- payment samples ------------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:6].upper()


def _seed() -> int:
    return uuid.uuid4().int % 1_000_000 + 1


def new_invoice(api: Api, *, amount: int = 4000) -> dict:
    return api.call(
        "admin",
        "POST",
        "/obligations",
        json={
            "learner_ref": f"learner-{_uid()}",
            "type": "instalment",
            "amount_minor": amount,
            "currency": "USD",
            "description": "Short course: instalment 1 of 3",
        },
    )


def import_statement(api: Api, txn: str, amount: str) -> None:
    row = {
        "date": dmy("14:35"),
        "narrative": "EcoCash payment received",
        "reference": txn,
        "credit": amount,
    }
    api.call(
        "reviewer",
        "POST",
        "/credits/imports",
        data={"account_alias": "MAIN", "profile": "generic"},
        files={"file": ("bank-statement.csv", statement_csv([row]), "text/csv")},
    )


@dataclass
class PaymentSample:
    invoice: dict
    png: bytes
    txn: str
    amount: str  # printed on the receipt
    note: str


def payment_sample(api: Api, variant: str) -> PaymentSample:
    """Create an invoice (and, where the story needs it, the matching bank record) and the receipt
    a payer would upload for it."""
    txn = f"PP{_uid()}.1432.A{_uid()}"
    sender = "Tendai Moyo"
    if variant == "matched":
        ob = new_invoice(api)
        import_statement(api, txn, "40.00")
        return PaymentSample(
            ob,
            api.receipt(ecocash_fields(txn=txn, amount="40.00", sender=sender)),
            txn,
            "40.00",
            "The bank statement already shows this payment.",
        )
    if variant == "unmatched":
        ob = new_invoice(api)
        return PaymentSample(
            ob,
            api.receipt(ecocash_fields(txn=txn, amount="40.00", sender=sender)),
            txn,
            "40.00",
            "No bank record yet: the receipt has to wait for the money.",
        )
    if variant == "edited":
        ob = new_invoice(api, amount=40_000)  # the invoice really is USD 400 ...
        import_statement(api, txn, "40.00")  # ... but the bank received 40.00
        return PaymentSample(
            ob,
            api.receipt(ecocash_fields(txn=txn, amount="400.00", sender=sender)),
            txn,
            "400.00",
            "The receipt says 400.00; the bank received 40.00.",
        )
    if variant == "short":
        ob = new_invoice(api)
        import_statement(api, txn, "30.00")
        return PaymentSample(
            ob,
            api.receipt(ecocash_fields(txn=txn, amount="30.00", sender=sender)),
            txn,
            "30.00",
            "Only USD 30.00 of the USD 40.00 invoice was paid.",
        )
    if variant == "blurry":
        ob = new_invoice(api)
        return PaymentSample(
            ob,
            api.receipt(ecocash_fields(txn=txn, amount="40.00", sender=sender), blur=9),
            txn,
            "40.00",
            "A photo that is too blurry to read.",
        )
    raise KeyError(variant)


def submit_receipt(api: Api, s: PaymentSample) -> dict:
    claim = api.call(
        "payer",
        "POST",
        "/claims",
        data={
            "obligation_id": s.invoice["obligation_id"],
            "claimant_ref": s.invoice["learner_ref"],
            "channel": "ecocash",
        },
        files={"file": ("receipt.png", s.png, "image/png")},
    )
    return api.call("reviewer", "GET", f"/claims/{claim['claim_id']}")


def _payer_view(api: Api, claim_id: str) -> list[str]:
    """What the payer is told about the claim (never the internal reasons or risk signals)."""
    return list(api.call("payer", "GET", f"/claims/{claim_id}").get("messages") or [])


def _pay(
    api: Api, scenario: str, variant: str, headline: str, steps: list[Step], followup: bool = False
) -> Result:
    s = payment_sample(api, variant)
    c = submit_receipt(api, s)
    follow = []
    if followup:
        follow = [
            {
                "id": "deliver-money",
                "label": "Simulate the bank statement arriving",
                "params": {"claim_id": c["claim_id"], "txn": s.txn, "amount": s.amount},
            }
        ]
    return Result(
        scenario,
        "payment",
        c["claim_id"],
        c["state"],
        headline,
        steps,
        follow,
        c["reason_codes"],
        _payer_view(api, c["claim_id"]),
    )


def _pay_clear(api: Api) -> Result:
    return _pay(
        api,
        "pay-clear",
        "matched",
        "Approved in seconds, with nobody touching it.",
        [
            Step("payer", "Uploads the receipt for a USD 40.00 instalment."),
            Step("system", "Two readers extract the amount, reference and date."),
            Step(
                "system",
                "The bank statement already shows that exact credit: confirmed by the money, not the picture.",
            ),
            Step(
                "system",
                "Every rule passes (small amount, exact payment, no risk signals), so the invoice is paid.",
            ),
        ],
    )


def _pay_waiting(api: Api) -> Result:
    return _pay(
        api,
        "pay-waiting",
        "unmatched",
        "A receipt alone is never proof. It waits for the money.",
        [
            Step("payer", "Uploads a receipt, but the money has not reached the bank yet."),
            Step("system", "The receipt looks consistent, but a screenshot is only a claim."),
            Step(
                "system",
                "The claim waits. Provisional access can bridge the gap if the organisation allows it.",
            ),
        ],
        followup=True,
    )


def _pay_edited(api: Api) -> Result:
    return _pay(
        api,
        "pay-edited",
        "edited",
        "The receipt says 400.00. The bank says 40.00. Caught.",
        [
            Step("payer", "Uploads a receipt showing USD 400.00."),
            Step("system", "The bank record for the same reference is USD 40.00."),
            Step(
                "system",
                "An amount-edit signal is raised, the claim is held and an administrator is alerted.",
            ),
            Step("admin", "Sees it under Suspected fakes, with the reasons in plain language."),
        ],
    )


def _pay_reused(api: Api) -> Result:
    first = payment_sample(api, "unmatched")
    submit_receipt(api, first)
    second = payment_sample(api, "unmatched")
    second.png = first.png  # the very same file, for a different invoice
    c = submit_receipt(api, second)
    return Result(
        "pay-reused",
        "payment",
        c["claim_id"],
        c["state"],
        "The same receipt, used for a second invoice.",
        [
            Step("payer", "Uploads a receipt for one invoice."),
            Step("payer", "Uploads the very same file for a different invoice."),
            Step(
                "system",
                "The file fingerprint matches an earlier claim. Both go to an administrator; the payer is only told it is being checked.",
            ),
        ],
        reasons=c["reason_codes"],
        payer_messages=_payer_view(api, c["claim_id"]),
    )


def _pay_short(api: Api) -> Result:
    return _pay(
        api,
        "pay-short",
        "short",
        "Paid USD 30 of USD 40. A person decides.",
        [
            Step("payer", "Pays USD 30.00 against a USD 40.00 invoice."),
            Step(
                "system", "The money is real, but only an exact payment is settled automatically."
            ),
            Step(
                "system",
                "It is left in the review queue for a finance reviewer to confirm as a part-payment.",
            ),
        ],
    )


def _pay_blurry(api: Api) -> Result:
    return _pay(
        api,
        "pay-blurry",
        "blurry",
        "A blurry photo is sent back, not guessed at.",
        [
            Step("payer", "Uploads a blurry photo of the receipt."),
            Step("system", "The picture is too soft to read reliably, so nothing is guessed."),
            Step("system", "The payer is asked to retake it, in plain language."),
        ],
    )


def deliver_money(api: Api, *, claim_id: str, txn: str, amount: str) -> Result:
    """Follow-up for `pay-waiting`: the bank statement arrives and the claim is re-matched."""
    import_statement(api, txn, amount)
    c = api.call("reviewer", "GET", f"/claims/{claim_id}")
    return Result(
        "pay-waiting",
        "payment",
        claim_id,
        c["state"],
        "The money arrived, so the claim was confirmed and approved.",
        [
            Step("system", "A bank statement with this exact reference was imported."),
            Step("system", "The waiting claim was matched to it and the rules ran again."),
            Step("system", "Money confirmed on our side, every rule passes: approved."),
        ],
        reasons=c["reason_codes"],
        payer_messages=_payer_view(api, claim_id),
    )


# --- identity samples -----------------------------------------------------------------------------


def _id_number() -> str:
    return f"VD-{uuid.uuid4().int % 9_000_000 + 1_000_000}"


def _passport_number() -> str:
    return f"VP{uuid.uuid4().int % 9_000_000 + 1_000_000}"


@dataclass
class KycSample:
    front: bytes
    selfie: bytes
    note: str


def kyc_sample(variant: str, *, number: str | None = None) -> KycSample:
    num = number or _id_number()
    if variant == "clear":
        return KycSample(
            art.id_card_png(art.id_fields("tendai", num), "tendai", seed=_seed()),
            art.selfie_png("tendai", seed=_seed()),
            "A valid ID and a matching selfie.",
        )
    if variant == "mismatch":
        return KycSample(
            art.id_card_png(art.id_fields("tendai", num), "tendai", seed=_seed()),
            art.selfie_png("farai", seed=_seed()),
            "Tendai's ID with someone else's selfie.",
        )
    if variant == "expired":
        return KycSample(
            art.id_card_png(
                art.id_fields("naledi", num, expiry="30/06/2023"), "naledi", seed=_seed()
            ),
            art.selfie_png("naledi", seed=_seed()),
            "A matching selfie, but the card expired in 2023.",
        )
    if variant == "noface":
        return KycSample(
            art.id_card_png(art.id_fields("amina", num), "amina", seed=_seed()),
            art.selfie_png(None, seed=_seed()),
            "A selfie that is a photo of a plant.",
        )
    if variant == "shared":
        return KycSample(
            art.id_card_png(art.id_fields("liam", num), "liam", seed=_seed()),
            art.selfie_png("liam", seed=_seed()),
            "An ID number that is already in use.",
        )
    if variant == "passport":
        return KycSample(
            art.passport_png("farai", _passport_number()),
            art.selfie_png("farai", seed=_seed()),
            "A passport with a machine-readable zone.",
        )
    if variant == "edited":
        return KycSample(
            art.passport_png(
                "kuda", _passport_number(), expiry="30/06/2040", mrz_expiry="30/06/2026"
            ),
            art.selfie_png("kuda", seed=_seed()),
            "The printed expiry says 2040; the checked zone says 2026.",
        )
    raise KeyError(variant)


def submit_kyc(api: Api, s: KycSample, subject: str | None = None) -> dict:
    out = api.call(
        "payer",
        "POST",
        "/kyc/verifications",
        data={"subject_type": "payer", "subject_ref": subject or f"learner-{_uid()}"},
        files={
            "front": ("id-front.png", s.front, "image/png"),
            "selfie": ("selfie.png", s.selfie, "image/png"),
        },
    )
    return api.call("reviewer", "GET", f"/kyc/verifications/{out['kyc_id']}")


def _kyc(api: Api, scenario: str, variant: str, headline: str, steps: list[Step]) -> Result:
    d = submit_kyc(api, kyc_sample(variant))
    return Result(
        scenario,
        "kyc",
        d["kyc_id"],
        d["state"],
        headline,
        steps,
        reasons=d["reason_codes"],
        payer_messages=[d["message"]] if d.get("message") else [],
    )


def _kyc_clear(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-clear",
        "clear",
        "Verified in seconds, automatically.",
        [
            Step("payer", "Photographs the ID card and takes a selfie."),
            Step("system", "Finds the card in the photo and straightens it."),
            Step("system", "Two readers read the name, number, birth date and expiry, and agree."),
            Step(
                "system",
                "The ID photo matches the selfie, and the selfie looks like a live capture.",
            ),
            Step("system", "All rules pass: verified. A sample of these is audited by a person."),
        ],
    )


def _kyc_passport(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-passport",
        "passport",
        "A passport, checked against its own check digits.",
        [
            Step("payer", "Submits a passport photo page and a selfie."),
            Step("system", "Reads the machine-readable zone and validates every check digit."),
            Step("system", "The printed fields agree with the zone, the face matches: verified."),
        ],
    )


def _kyc_mismatch(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-mismatch",
        "mismatch",
        "The selfie is not the person on the ID.",
        [
            Step("payer", "Submits Tendai's ID card with someone else's selfie."),
            Step("system", "The face match score is far below the threshold."),
            Step(
                "system",
                "Nothing is rejected automatically: it goes to a reviewer, who sees both pictures side by side.",
            ),
        ],
    )


def _kyc_expired(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-expired",
        "expired",
        "An expired ID is never auto-verified.",
        [
            Step("payer", "Submits a card that expired in 2023, with a matching selfie."),
            Step("system", "Everything else checks out, but the document is past its expiry date."),
            Step("system", "A reviewer decides, with the reason shown."),
        ],
    )


def _kyc_edited(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-edited",
        "edited",
        "The printed expiry was edited. The checked zone disagrees.",
        [
            Step("payer", "Submits a passport whose printed expiry says 2040."),
            Step("system", "The machine-readable zone, protected by check digits, says 2026."),
            Step(
                "system",
                "Printed text contradicting the zone is a conflict, so a reviewer decides.",
            ),
        ],
    )


def _kyc_shared(api: Api) -> Result:
    num = _id_number()
    submit_kyc(api, kyc_sample("shared", number=num))  # the first person: accepted
    d = submit_kyc(api, kyc_sample("shared", number=num))  # a second person, same ID number
    return Result(
        "kyc-shared",
        "kyc",
        d["kyc_id"],
        d["state"],
        "One ID number, two people.",
        [
            Step("payer", "A learner verifies with an ID card. It is accepted."),
            Step("payer", "A different learner submits a card with the same ID number."),
            Step(
                "system", "The number is already tied to another person, so it goes to a reviewer."
            ),
        ],
        reasons=d["reason_codes"],
        payer_messages=[d["message"]] if d.get("message") else [],
    )


def _kyc_noface(api: Api) -> Result:
    return _kyc(
        api,
        "kyc-noface",
        "noface",
        "No face in the selfie: asked to retake it.",
        [
            Step("payer", "Uploads a selfie that is actually a photo of a plant."),
            Step("system", "No face is found. This is a capture problem, not a verdict."),
            Step("system", "The payer is told how to retake it. A reviewer is not bothered."),
        ],
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        "pay-clear",
        "payments",
        "Pay and get approved",
        "The money is already in the bank. The receipt is approved in seconds.",
        "The receipt is only a claim; the bank record is the proof. When both agree and every rule passes, nobody has to touch it.",
        "APPROVED",
        "AUTO_APPROVED",
        _pay_clear,
    ),
    Scenario(
        "pay-waiting",
        "payments",
        "Receipt before the money",
        "A receipt alone is never enough. Then the money arrives.",
        "Screenshots are easy to fake, so the claim waits until the money shows up on our side.",
        "AWAITING_FINANCIAL_CONFIRMATION",
        "AWAITING_CREDIT",
        _pay_waiting,
    ),
    Scenario(
        "pay-edited",
        "payments",
        "Edited receipt",
        "The receipt shows 400.00, the bank shows 40.00.",
        "A fake receipt is caught by comparing it with the money, not by trusting the picture.",
        "AWAITING_FINANCIAL_CONFIRMATION",
        "SUSPECTED_FAKE",
        _pay_edited,
    ),
    Scenario(
        "pay-reused",
        "payments",
        "Same receipt, twice",
        "One receipt submitted for two different invoices.",
        "Reuse is detected by fingerprint, and the payer is never accused, only held.",
        "DUPLICATE_HOLD",
        "DUPLICATE_FILE",
        _pay_reused,
    ),
    Scenario(
        "pay-short",
        "payments",
        "Paid part of the amount",
        "USD 30 paid on a USD 40 invoice goes to a person.",
        "Automation only handles clear cases; anything that needs judgement is left for a reviewer.",
        "FINANCIALLY_CONFIRMED",
        "",
        _pay_short,
    ),
    Scenario(
        "pay-blurry",
        "payments",
        "Blurry photo",
        "Too soft to read, so the payer is asked to retake it.",
        "Bad captures are sent back with plain instructions; nothing is guessed.",
        "NEEDS_INFORMATION",
        "CAPTURE_BLURRY",
        _pay_blurry,
    ),
    Scenario(
        "kyc-clear",
        "identity",
        "Verify an ID in seconds",
        "ID card plus selfie: read, matched and verified automatically.",
        "Two readers must agree, the face must match and the document must be valid.",
        "VERIFIED",
        "AUTO_VERIFIED",
        _kyc_clear,
    ),
    Scenario(
        "kyc-passport",
        "identity",
        "Passport with MRZ",
        "The machine-readable zone is checked digit by digit.",
        "The strongest signal is mathematical: every field in the zone has a check digit.",
        "VERIFIED",
        "AUTO_VERIFIED",
        _kyc_passport,
    ),
    Scenario(
        "kyc-mismatch",
        "identity",
        "Someone else's selfie",
        "The face on the ID does not match the selfie.",
        "Mismatches are never auto-rejected: a reviewer sees both pictures and decides.",
        "NEEDS_REVIEW",
        "FACE_SCORE_LOW",
        _kyc_mismatch,
    ),
    Scenario(
        "kyc-expired",
        "identity",
        "Expired ID",
        "Everything matches, but the card has expired.",
        "Hard rules (expiry, country, type) are not left to a model's opinion.",
        "NEEDS_REVIEW",
        "DOC_EXPIRED",
        _kyc_expired,
    ),
    Scenario(
        "kyc-edited",
        "identity",
        "Edited passport",
        "The printed expiry disagrees with the checked zone.",
        "A forged field is caught because two independent sources disagree.",
        "NEEDS_REVIEW",
        "FIELD_CONFLICT",
        _kyc_edited,
    ),
    Scenario(
        "kyc-shared",
        "identity",
        "One ID, two people",
        "Another learner presents an ID number already in use.",
        "Identity checks look across people, not only at one submission.",
        "NEEDS_REVIEW",
        "ID_NUMBER_USED_BY_OTHER_SUBJECT",
        _kyc_shared,
    ),
    Scenario(
        "kyc-noface",
        "identity",
        "No face in the selfie",
        "A photo of a plant: asked to retake it.",
        "Capture problems go back to the person, so reviewers only see real decisions.",
        "NEEDS_INFORMATION",
        "NO_FACE_IN_SELFIE",
        _kyc_noface,
    ),
]
BY_ID = {s.id: s for s in SCENARIOS}
PAYMENT_VARIANTS = ("matched", "unmatched", "edited", "short", "blurry")
KYC_VARIANTS = ("clear", "mismatch", "expired", "noface", "shared", "passport", "edited")

# the order in which a fresh demo is populated, so every console page has something in it
SEED_ORDER = [
    "pay-clear",
    "pay-clear",
    "pay-waiting",
    "pay-edited",
    "pay-reused",
    "pay-short",
    "kyc-clear",
    "kyc-clear",
    "kyc-mismatch",
    "kyc-expired",
    "kyc-edited",
    "kyc-shared",
]
