"""Guardrails on the AI readers: locked system guardrails plus admin-managed rules.

Design rule: a guardrail can only make the pipeline *stricter*. Its actions are limited to
sending a claim to review, asking the payer for information, distrusting a field, switching the
AI reader off, or giving the reader extra reading hints. Nothing here can approve, allocate,
relax a hard gate or override the ledger. Those paths do not consult guardrails at all.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.extraction.agreement import FinalField
from app.extraction.schema import FIELD_NAMES
from app.models import GuardrailRule

# Locked guardrails: enforced in code, listed so reviewers and auditors can see them.
SYSTEM_GUARDRAILS: list[dict[str, str]] = [
    {
        "id": "system:ai-never-approves",
        "name": "AI never approves",
        "description": "AI output cannot approve, allocate or grant access. Only a reviewer can "
        "confirm, and only against a recipient-side credit that passes every hard gate.",
    },
    {
        "id": "system:schema-only-output",
        "name": "Schema-only output",
        "description": "The vision model must return the fixed receipt JSON schema. Its text is "
        "data; it has no tools and is never read as an instruction.",
    },
    {
        "id": "system:untrusted-evidence",
        "name": "Evidence is untrusted",
        "description": "Text inside receipts, QR codes and messages is treated as untrusted. The "
        "model is told to ignore instructions in the image.",
    },
    {
        "id": "system:two-reader-agreement",
        "name": "Two readers for critical fields",
        "description": "Amount, reference, date and beneficiary count only when two independent "
        "readers agree, or a reviewer corrects them.",
    },
    {
        "id": "system:fail-closed",
        "name": "Fail closed",
        "description": "Reader errors, timeouts, low confidence or a missing credit route the "
        "claim to a human. They never approve.",
    },
    {
        "id": "system:ledger-invariants",
        "name": "Ledger invariants",
        "description": "The database rejects over-allocation, currency mixing, unsettled credits "
        "and any edit to allocations, decisions or audit events.",
    },
    {
        "id": "system:stricter-only",
        "name": "Guardrails only tighten",
        "description": "Admin rules can escalate, distrust fields or switch AI off. No rule can "
        "relax a gate or approve a claim.",
    },
]

Kind = Literal[
    "field_pattern",
    "amount_range",
    "blocked_text",
    "require_field",
    "min_confidence",
    "prompt_instruction",
    "model_allowlist",
    "ai_kill_switch",
    "external_processing_block",
]
Action = Literal["review", "needs_info", "disable_ai", "prompt"]

_FIELD_KINDS = {"field_pattern", "amount_range", "blocked_text", "require_field", "min_confidence"}
_DISABLE_KINDS = {"model_allowlist", "ai_kill_switch", "external_processing_block"}
MAX_PATTERN = 200
MAX_PROMPT = 500


def _check_regex(p: str) -> str:
    if len(p) > MAX_PATTERN:
        raise ValueError(f"pattern longer than {MAX_PATTERN} characters")
    try:
        re.compile(p)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc
    return p


def _check_field(f: str) -> str:
    if f not in FIELD_NAMES:
        raise ValueError(f"field must be one of {list(FIELD_NAMES)}")
    return f


class _FieldRule(BaseModel):
    field: str

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        return _check_field(v)


class FieldPattern(_FieldRule):
    pattern: str
    provider: str | None = None  # only apply when this provider was read (e.g. ECOCASH)

    @field_validator("pattern")
    @classmethod
    def _regex(cls, v: str) -> str:
        return _check_regex(v)


class AmountRange(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    min_minor: int | None = Field(None, ge=0)
    max_minor: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _bounds(self) -> "AmountRange":
        if self.min_minor is None and self.max_minor is None:
            raise ValueError("set min_minor, max_minor or both")
        if self.min_minor is not None and self.max_minor is not None:
            if self.min_minor > self.max_minor:
                raise ValueError("min_minor is above max_minor")
        return self


class BlockedText(BaseModel):
    patterns: list[str] = Field(min_length=1, max_length=50)
    fields: list[str] | None = None  # default: every field

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        return [_check_regex(p) for p in v]

    @field_validator("fields")
    @classmethod
    def _fields(cls, v: list[str] | None) -> list[str] | None:
        return [_check_field(f) for f in v] if v else v


class RequireField(_FieldRule):
    must_agree: bool = True


class MinConfidence(_FieldRule):
    min: float = Field(gt=0, le=1)


class PromptInstruction(BaseModel):
    text: str = Field(min_length=3, max_length=MAX_PROMPT)

    @field_validator("text")
    @classmethod
    def _plain(cls, v: str) -> str:
        # hints are reading guidance, not a second prompt: one paragraph, no markup tricks
        cleaned = " ".join(v.replace("`", "'").split())
        if re.search(
            r"\b(approve|allocate|ignore (?:the|all|previous)|schema|json)\b", cleaned, re.I
        ):
            raise ValueError("hints may describe how to read receipts, not change output or rules")
        return cleaned


class ModelAllowlist(BaseModel):
    models: list[str] = Field(min_length=1)


class Empty(BaseModel):
    pass


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "field_pattern": FieldPattern,
    "amount_range": AmountRange,
    "blocked_text": BlockedText,
    "require_field": RequireField,
    "min_confidence": MinConfidence,
    "prompt_instruction": PromptInstruction,
    "model_allowlist": ModelAllowlist,
    "ai_kill_switch": Empty,
    "external_processing_block": Empty,
}


class RuleIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    kind: Kind
    action: Action
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = Field("", max_length=1000)

    @model_validator(mode="after")
    def _consistent(self) -> "RuleIn":
        if self.kind in _FIELD_KINDS and self.action not in ("review", "needs_info"):
            raise ValueError(f"{self.kind} rules must use action review or needs_info")
        if self.kind in _DISABLE_KINDS and self.action != "disable_ai":
            raise ValueError(f"{self.kind} rules must use action disable_ai")
        if self.kind == "prompt_instruction" and self.action != "prompt":
            raise ValueError("prompt_instruction rules must use action prompt")
        try:
            self.config = CONFIG_MODELS[self.kind].model_validate(self.config).model_dump()
        except ValidationError as exc:
            raise ValueError(f"invalid config for {self.kind}: {exc.errors()[0]['msg']}") from exc
        return self


# Recommended rules admins can add with one click (then edit or remove).
TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Prompt-injection phrases",
        "kind": "blocked_text",
        "action": "review",
        "description": "Text on a receipt that addresses the AI or the reviewer is a red flag.",
        "config": {
            "patterns": [
                r"ignore (all|previous|the above)",
                r"\bsystem\s*:",
                r"\bapprove (this|the) payment",
                r"\bas an ai\b",
                r"\bdisregard\b.*\binstructions?\b",
            ]
        },
    },
    {
        "name": "EcoCash reference format",
        "kind": "field_pattern",
        "action": "review",
        "description": "Distrust EcoCash references that do not match the known layout. Confirm "
        "the pattern against real Phase 0 samples before relying on it.",
        "config": {
            "field": "transaction_id",
            "provider": "ECOCASH",
            "pattern": r"(?:PP|MP|CO)\d{6}\.\d{4}\.[A-Z0-9]{6}",
        },
    },
    {
        "name": "USD amount sanity range",
        "kind": "amount_range",
        "action": "review",
        "description": "Amounts far outside normal course fees go to a reviewer.",
        "config": {"currency": "USD", "min_minor": 100, "max_minor": 500_000},
    },
    {
        "name": "Transaction reference required",
        "kind": "require_field",
        "action": "needs_info",
        "description": "Ask the payer for a clearer copy when no reference can be read.",
        "config": {"field": "transaction_id", "must_agree": False},
    },
    {
        "name": "Day-first dates",
        "kind": "prompt_instruction",
        "action": "prompt",
        "description": "Reading hint for Zimbabwean receipts.",
        "config": {
            "text": "Dates on these receipts are day-first (DD/MM/YYYY). Amounts use a dot "
            "as the decimal separator. ZiG may be printed as ZWG or ZiG."
        },
    },
    {
        "name": "Keep evidence on our servers",
        "kind": "external_processing_block",
        "action": "disable_ai",
        "description": "Do not send receipts to an external AI service until the data-protection "
        "review is signed off. Claims are read by OCR only and go to review.",
        "config": {},
    },
    {
        "name": "Approved models only",
        "kind": "model_allowlist",
        "action": "disable_ai",
        "description": "Switch the AI reader off if someone configures an unreviewed model.",
        "config": {"models": ["qwen3-vl:235b", "qwen2.5vl:7b"]},
    },
    {
        "name": "AI kill switch",
        "kind": "ai_kill_switch",
        "action": "disable_ai",
        "description": "Turn the AI reader off for this organisation immediately.",
        "config": {},
    },
]


def active_rules(session: Session, org_id: uuid.UUID) -> list[GuardrailRule]:
    return list(
        session.scalars(
            select(GuardrailRule)
            .where(
                GuardrailRule.org_id == org_id,
                GuardrailRule.removed_at.is_(None),
                GuardrailRule.enabled.is_(True),
            )
            .order_by(GuardrailRule.created_at)
        )
    )


def reason_code(rule: GuardrailRule) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", rule.name.upper()).strip("_")
    return f"GUARDRAIL_{slug}"[:60]


def is_external(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return not (
        host in ("localhost", "127.0.0.1", "::1", "ollama")
        or host.endswith(".local")
        or host.endswith(".internal")
        or re.match(r"^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.", host) is not None
    )


@dataclass
class ReaderPlan:
    """Decided before extraction: may the VLM run, and with which extra hints."""

    vlm_allowed: bool
    hints: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    applied: list[dict[str, str]] = field(default_factory=list)


def plan_readers(rules: list[GuardrailRule], vlm_url: str, vlm_model: str) -> ReaderPlan:
    plan = ReaderPlan(vlm_allowed=True)
    for r in rules:
        disable = False
        if r.kind == "ai_kill_switch":
            disable = True
        elif r.kind == "external_processing_block" and is_external(vlm_url):
            disable = True
        elif r.kind == "model_allowlist" and vlm_model not in r.config["models"]:
            disable = True
        elif r.kind == "prompt_instruction":
            plan.hints.append(r.config["text"])
            plan.applied.append({"rule_id": str(r.rule_id), "name": r.name, "effect": "hint"})
        if disable:
            plan.vlm_allowed = False
            plan.reasons.append(reason_code(r))
            plan.applied.append({"rule_id": str(r.rule_id), "name": r.name, "effect": "ai_off"})
    return plan


@dataclass
class FieldVerdict:
    review: list[str] = field(default_factory=list)
    needs_info: list[str] = field(default_factory=list)
    distrusted: set[str] = field(default_factory=set)
    applied: list[dict[str, str]] = field(default_factory=list)


def evaluate_fields(
    rules: list[GuardrailRule],
    fields: dict[str, FinalField],
    declared_amount_minor: int | None,
    declared_currency: str | None,
) -> FieldVerdict:
    """Apply field-level guardrails to the combined readings. Pure; the caller persists."""
    v = FieldVerdict()
    provider = fields["provider"].normalised if "provider" in fields else None

    def hit(rule: GuardrailRule, distrust: str | None = None, detail: str = "") -> None:
        code = reason_code(rule)
        (v.needs_info if rule.action == "needs_info" else v.review).append(code)
        if distrust:
            v.distrusted.add(distrust)
        v.applied.append(
            {
                "rule_id": str(rule.rule_id),
                "name": rule.name,
                "effect": "hit",
                "detail": detail[:200],
            }
        )

    for r in rules:
        c = r.config
        if r.kind == "field_pattern":
            if c.get("provider") and c["provider"].upper() != (provider or ""):
                continue
            f = fields.get(c["field"])
            if f and f.raw and not re.fullmatch(c["pattern"], f.raw.strip(), re.I):
                hit(r, c["field"], f"{c['field']}={f.raw[:60]!r}")
        elif r.kind == "amount_range":
            amount, currency = None, declared_currency
            f = fields.get("amount")
            cur = fields.get("currency")
            if f and f.normalised and f.agreed:
                amount = int(f.normalised)
                currency = (cur.normalised if cur and cur.normalised else None) or currency
            elif declared_amount_minor is not None:
                amount = declared_amount_minor
            if amount is None or (currency or "").upper() != c["currency"].upper():
                continue
            if (c.get("min_minor") is not None and amount < c["min_minor"]) or (
                c.get("max_minor") is not None and amount > c["max_minor"]
            ):
                hit(r, None, f"amount {amount} {currency}")
        elif r.kind == "blocked_text":
            names = c.get("fields") or list(fields)
            for name in names:
                f = fields.get(name)
                text = (f.raw or "")[:500] if f else ""
                if text and any(re.search(p, text, re.I) for p in c["patterns"]):
                    hit(r, None, f"{name} matched")
                    break
        elif r.kind == "require_field":
            f = fields.get(c["field"])
            missing = f is None or not f.raw or (c.get("must_agree") and not f.agreed)
            if missing:
                hit(r, None, f"{c['field']} missing")
        elif r.kind == "min_confidence":
            f = fields.get(c["field"])
            if f and f.extractor != "reviewer" and f.confidence < c["min"]:
                hit(r, c["field"], f"{c['field']} confidence {f.confidence}")
    return v


def add_rule(session: Session, org_id: uuid.UUID, body: RuleIn, actor: str) -> GuardrailRule:
    rule = GuardrailRule(
        org_id=org_id,
        name=body.name,
        kind=body.kind,
        action=body.action,
        config=body.config,
        description=body.description,
        created_by=actor,
    )
    session.add(rule)
    session.flush()
    audit(
        session,
        actor=actor,
        action="guardrail.added",
        entity_type="guardrail",
        entity_id=rule.rule_id,
        org_id=org_id,
        after=body.model_dump(),
    )
    return rule


def set_enabled(session: Session, rule: GuardrailRule, enabled: bool, actor: str) -> None:
    before = {"enabled": rule.enabled}
    rule.enabled = enabled
    audit(
        session,
        actor=actor,
        action="guardrail.enabled" if enabled else "guardrail.disabled",
        entity_type="guardrail",
        entity_id=rule.rule_id,
        org_id=rule.org_id,
        before=before,
        after={"enabled": enabled},
    )


def remove_rule(session: Session, rule: GuardrailRule, actor: str, reason: str) -> None:
    rule.removed_at = datetime.now(UTC)
    rule.removed_by = actor
    audit(
        session,
        actor=actor,
        action="guardrail.removed",
        entity_type="guardrail",
        entity_id=rule.rule_id,
        org_id=rule.org_id,
        before={"name": rule.name, "kind": rule.kind, "config": rule.config},
        after={"reason": reason},
    )
