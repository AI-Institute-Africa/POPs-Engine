"""Guardrails and policies for the AI: view (reviewers), add / disable / remove (admins)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import Principal, Role, require
from app.guardrails.rules import (
    SYSTEM_GUARDRAILS,
    TEMPLATES,
    RuleIn,
    add_rule,
    is_external,
    plan_readers,
    remove_rule,
    set_enabled,
)
from app.models import GuardrailRule, PaymentPolicy
from app.policy.engine import PolicyConfig, load_policy, policy_snapshot

router = APIRouter(prefix="/admin", tags=["guardrails & policies"])
DB = Annotated[Session, Depends(get_session)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rule_id: uuid.UUID
    name: str
    kind: str
    action: str
    config: dict[str, Any]
    description: str
    enabled: bool
    created_by: str
    created_at: datetime
    removed_by: str | None
    removed_at: datetime | None


class RemoveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ToggleIn(BaseModel):
    enabled: bool


class PolicyIn(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    config: dict[str, Any]


def _rule(db: Session, p: Principal, rule_id: uuid.UUID) -> GuardrailRule:
    rule = db.scalars(
        select(GuardrailRule).where(
            GuardrailRule.rule_id == rule_id, GuardrailRule.org_id == p.org_id
        )
    ).first()
    if rule is None or rule.removed_at is not None:
        raise HTTPException(404, "guardrail not found")
    return rule


@router.get("/guardrails")
def list_guardrails(db: DB, p: Reviewer, include_removed: bool = False) -> dict:
    q = select(GuardrailRule).where(GuardrailRule.org_id == p.org_id)
    if not include_removed:
        q = q.where(GuardrailRule.removed_at.is_(None))
    rules = list(db.scalars(q.order_by(GuardrailRule.created_at)))
    s = get_settings()
    plan = plan_readers(
        [r for r in rules if r.enabled and not r.removed_at], s.ollama_url, s.ollama_model
    )
    return {
        "system": SYSTEM_GUARDRAILS,
        "rules": [RuleOut.model_validate(r).model_dump(mode="json") for r in rules],
        "reader": {
            "model": s.ollama_model if s.vlm_backend == "ollama" else s.vlm_backend,
            "external": s.vlm_backend == "ollama" and is_external(s.ollama_url),
            "ai_enabled": plan.vlm_allowed,
            "disabled_by": plan.reasons,
            "hints": plan.hints,
        },
    }


@router.get("/guardrails/templates")
def guardrail_templates(p: Reviewer) -> list[dict]:
    return TEMPLATES


@router.post("/guardrails", status_code=201)
def create_guardrail(body: dict[str, Any], db: DB, p: Admin) -> dict:
    try:
        rule_in = RuleIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    rule = add_rule(db, p.org_id, rule_in, p.actor)
    db.commit()
    return RuleOut.model_validate(rule).model_dump(mode="json")


@router.post("/guardrails/{rule_id}/enabled")
def toggle_guardrail(rule_id: uuid.UUID, body: ToggleIn, db: DB, p: Admin) -> dict:
    rule = _rule(db, p, rule_id)
    set_enabled(db, rule, body.enabled, p.actor)
    db.commit()
    return RuleOut.model_validate(rule).model_dump(mode="json")


@router.delete("/guardrails/{rule_id}")
def delete_guardrail(rule_id: uuid.UUID, body: RemoveIn, db: DB, p: Admin) -> dict:
    rule = _rule(db, p, rule_id)
    remove_rule(db, rule, p.actor, body.reason)
    db.commit()
    return RuleOut.model_validate(rule).model_dump(mode="json")


# --- payment policy versions ----------------------------------------------------------------


@router.get("/policy")
def current_policy(db: DB, p: Reviewer) -> dict:
    return {**policy_snapshot(load_policy(db, p.org_id)), "defaults": PolicyConfig().model_dump()}


@router.get("/policies")
def policy_history(db: DB, p: Reviewer) -> list[dict]:
    rows = db.scalars(
        select(PaymentPolicy)
        .where(PaymentPolicy.org_id == p.org_id)
        .order_by(PaymentPolicy.created_at.desc())
    )
    return [
        {
            "policy_id": r.policy_id,
            "version": r.version,
            "active": r.active,
            "created_by": r.created_by,
            "created_at": r.created_at,
            "config": r.config,
        }
        for r in rows
    ]


@router.post("/policy", status_code=201)
def publish_policy(body: PolicyIn, db: DB, p: Admin) -> dict:
    try:
        cfg = PolicyConfig.model_validate(body.config)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise HTTPException(422, f"{'.'.join(map(str, err['loc']))}: {err['msg']}") from exc
    if cfg.auto_approve_enabled:
        raise HTTPException(422, "auto-approval is not available in this release")
    if db.scalars(
        select(PaymentPolicy).where(
            PaymentPolicy.org_id == p.org_id, PaymentPolicy.version == body.version
        )
    ).first():
        raise HTTPException(409, "policy version already exists")
    db.add(
        PaymentPolicy(
            org_id=p.org_id, version=body.version, config=cfg.model_dump(), created_by=p.actor
        )
    )
    audit(
        db,
        actor=p.actor,
        action="policy.published",
        entity_type="policy",
        entity_id=body.version,
        org_id=p.org_id,
        after=cfg.model_dump(),
    )
    db.commit()
    return policy_snapshot(load_policy(db, p.org_id))


@router.post("/policies/{policy_id}/deactivate")
def deactivate_policy(policy_id: uuid.UUID, body: RemoveIn, db: DB, p: Admin) -> dict:
    """Retire a policy version; the previous active version (or the platform default) applies."""
    row = db.scalars(
        select(PaymentPolicy).where(
            PaymentPolicy.policy_id == policy_id, PaymentPolicy.org_id == p.org_id
        )
    ).first()
    if row is None or not row.active:
        raise HTTPException(404, "active policy version not found")
    row.active = False
    audit(
        db,
        actor=p.actor,
        action="policy.deactivated",
        entity_type="policy",
        entity_id=row.version,
        org_id=p.org_id,
        after={"reason": body.reason},
    )
    db.commit()
    return policy_snapshot(load_policy(db, p.org_id))
