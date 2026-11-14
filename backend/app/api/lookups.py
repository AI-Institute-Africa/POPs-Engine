"""Provider lookups API: a reviewer asks for one read-only check; the answer is evidence only."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.security import Principal, Role, require
from app.lookups import service
from app.models import Claim

router = APIRouter(tags=["lookups"])
DB = Annotated[Session, Depends(get_session)]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]


class LookupIn(BaseModel):
    url: str = Field(max_length=500)


@router.post("/claims/{claim_id}/provider-lookup")
def provider_lookup(claim_id: uuid.UUID, body: LookupIn, db: DB, p: Reviewer) -> dict:
    claim = db.get(Claim, claim_id)
    if claim is None or claim.org_id != p.org_id:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "No such claim."})
    try:
        out = service.run(db, claim, body.url, requested_by=p.email)
    except service.LookupError_ as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
    db.commit()
    return out
