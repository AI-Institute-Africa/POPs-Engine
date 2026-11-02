"""Provisional access reporting for admins."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.security import Principal, Role, require
from app.provisional.service import exposure_report

router = APIRouter(prefix="/admin", tags=["provisional access"])
DB = Annotated[Session, Depends(get_session)]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]


@router.get("/exposure")
def exposure(db: DB, p: Admin) -> dict:
    """Value of active provisional access, caps, recent outcomes, and grants ending soon."""
    return exposure_report(db, p.org_id)
