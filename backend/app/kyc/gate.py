"""Staff gate: approver-level actions need a valid KYC verification when the policy says so.

Applied to the approver role (second approvals, reversals). Admins are exempt on purpose: an admin
who is not yet verified must still be able to open the KYC console and fix the policy, otherwise
turning `kyc_required_for_staff` on could lock everyone out.
"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.security import Principal, Role, require
from app.kyc import service
from app.models import KycSubject
from app.policy.engine import load_policy


def require_cleared(role: Role):
    base = require(role)

    def dep(db: Session = Depends(get_session), p: Principal = Depends(base)) -> Principal:
        if p.at_least(Role.ADMIN):
            return p
        if load_policy(db, p.org_id).config.kyc_required_for_staff and not service.is_verified(
            db, p.org_id, KycSubject.STAFF, str(p.user_id)
        ):
            raise HTTPException(
                403,
                {"code": "KYC_REQUIRED", "message": "complete identity verification first"},
            )
        return p

    return dep
