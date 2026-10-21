import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    org_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    correlation_id: uuid.UUID | None = None,
) -> None:
    """Append an audit event in the caller's transaction (never committed on its own)."""
    session.add(
        AuditEvent(
            org_id=org_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            before=before,
            after=after,
            correlation_id=correlation_id,
        )
    )
