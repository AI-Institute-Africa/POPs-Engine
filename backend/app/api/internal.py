"""Host-system provisioning. Not part of the public API.

The engine has no user or organisation management of its own: a host system (Cognify) signs the
tokens it sends, and the `org` claim must name an organisation that already exists. This router
lets that host create one on demand. It does exactly one thing, find-or-create an organisation by
name, and it only exists when POPS_PROVISIONING_KEY is set (otherwise every call is a 404, so the
feature is indistinguishable from a missing route). Keep /internal off the public internet.
"""

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.db import get_session
from app.models import Organisation

router = APIRouter(prefix="/internal", tags=["internal"])
DB = Annotated[Session, Depends(get_session)]


def require_provisioning_key(
    x_pops_provisioning_key: Annotated[str | None, Header()] = None,
) -> None:
    key = get_settings().provisioning_key
    if not key:
        raise HTTPException(404)
    # compare as bytes: compare_digest rejects non-ASCII str, and a header can carry anything
    if not x_pops_provisioning_key or not hmac.compare_digest(
        x_pops_provisioning_key.encode(), key.encode()
    ):
        raise HTTPException(401, "invalid provisioning key")


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgOut(BaseModel):
    org_id: uuid.UUID
    name: str
    created: bool


@router.post("/orgs", response_model=OrgOut, dependencies=[Depends(require_provisioning_key)])
def provision_org(body: OrgIn, db: DB) -> OrgOut:
    """Return the organisation with this exact name, creating it first if there is none.

    Safe to repeat and safe to call concurrently: callers asking for the same name queue on a
    transaction-scoped advisory lock, so two requests can never both create it.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank")
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"pops:org:{name}"},
    )
    org = db.scalars(
        select(Organisation).where(Organisation.name == name).order_by(Organisation.created_at)
    ).first()
    created = org is None
    if org is None:
        org = Organisation(name=name)
        db.add(org)
        db.flush()
        audit(
            db,
            actor="system:provisioning",
            action="organisation.provisioned",
            entity_type="organisation",
            entity_id=org.org_id,
            org_id=org.org_id,
            after={"name": name},
        )
    db.commit()
    return OrgOut(org_id=org.org_id, name=org.name, created=created)
