"""Background jobs. Every task is idempotent and safe to retry."""

import logging
import uuid

from celery import Celery
from sqlalchemy import func, select

from app.core import observability as obs
from app.core.config import get_settings
from app.core.db import session_scope

log = logging.getLogger(__name__)

celery_app = Celery("pops", broker=get_settings().redis_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "deliver-outbox": {"task": "pops.deliver_outbox", "schedule": 30.0},
        "sweep-stuck-claims": {"task": "pops.sweep_stuck", "schedule": 60.0},
        "poll-alert-mailbox": {"task": "pops.poll_alert_mailbox", "schedule": 60.0},
    },
)


class PipelineFailed(Exception):
    pass


def run_pipeline(claim_id: uuid.UUID) -> bool:
    """Process one claim. On error, record the attempt in its own transaction.

    Returns True when the claim was processed; False when it failed (the sweeper or Celery
    retries it; after the attempt limit the claim goes to a reviewer as PIPELINE_FAILED).
    """
    from app.workers.pipeline import process_claim, record_failure

    try:
        with session_scope() as session:
            process_claim(session, claim_id)
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, never lost
        log.exception("pipeline failed for claim %s", claim_id)
        with session_scope() as session:
            record_failure(session, claim_id, f"{type(exc).__name__}: {exc}")
        return False
    dispatch_shadow(claim_id)  # advisory only, never affects the claim
    return True


def run_shadow(claim_id: uuid.UUID) -> int:
    """Record shadow-mode proposals for a claim. Fail-soft: errors are logged, never raised."""
    from app.shadow.proposer import propose_for_claim

    try:
        with session_scope() as session:
            return len(propose_for_claim(session, claim_id))
    except Exception:  # noqa: BLE001 - shadow mode must never break or retry the pipeline
        log.exception("shadow proposal failed for claim %s", claim_id)
        obs.SHADOW_ERRORS.inc()
        return 0


def run_rematch(org_id: uuid.UUID) -> int:
    """Credit-first matching, then flag claims whose money should have arrived by now."""
    from app.workers.pipeline import rematch_awaiting, scan_missing_credits

    with session_scope() as session:
        matched = rematch_awaiting(session, org_id)
        scan_missing_credits(session, org_id)
        return matched


def sweep_stuck() -> int:
    """Re-dispatch claims whose pipeline task was lost or failed; update the stuck gauge."""
    from app.models import OutboxEvent
    from app.workers.pipeline import stuck_claims

    with session_scope() as session:
        stuck = [c.claim_id for c in stuck_claims(session)]
        pending = session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")
        )
    from app.alerts.service import check_stale_sources

    with session_scope() as session:
        check_stale_sources(session)
    obs.STUCK_CLAIMS.set(len(stuck))
    obs.OUTBOX_PENDING.set(pending or 0)
    if stuck:
        log.warning("re-dispatching %d stuck claims", len(stuck))
    for claim_id in stuck:
        dispatch_pipeline(claim_id)
    return len(stuck)


@celery_app.task(name="pops.process_claim", bind=True, max_retries=3)
def process_claim_task(self, claim_id: str) -> None:
    if not run_pipeline(uuid.UUID(claim_id)):
        raise self.retry(countdown=30 * (self.request.retries + 1))


@celery_app.task(name="pops.rematch", autoretry_for=(Exception,), max_retries=3)
def rematch_task(org_id: str) -> int:
    return run_rematch(uuid.UUID(org_id))


@celery_app.task(name="pops.shadow_propose", autoretry_for=(Exception,), max_retries=2)
def shadow_propose_task(claim_id: str) -> int:
    return run_shadow(uuid.UUID(claim_id))


@celery_app.task(name="pops.deliver_outbox")
def deliver_outbox_task() -> int:
    from app.entitlements.outbox import deliver_pending

    with session_scope() as session:
        return deliver_pending(session)


@celery_app.task(name="pops.poll_alert_mailbox")
def poll_alert_mailbox_task() -> int:
    from app.alerts.service import poll_imap
    from app.models import AlertSource

    with session_scope() as session:
        count = poll_imap(session)
        org = None
        if count and get_settings().alert_imap_source_id:
            src = session.get(AlertSource, uuid.UUID(get_settings().alert_imap_source_id))
            org = src.org_id if src else None
    if org:
        dispatch_rematch(org)
    return count


@celery_app.task(name="pops.sweep_stuck")
def sweep_stuck_task() -> int:
    return sweep_stuck()


def dispatch_pipeline(claim_id: uuid.UUID) -> None:
    if get_settings().pipeline_mode == "celery":
        try:
            process_claim_task.delay(str(claim_id))
        except Exception:  # noqa: BLE001 - broker down: the sweeper picks the claim up later
            log.exception("could not enqueue claim %s; sweeper will retry", claim_id)
    else:
        run_pipeline(claim_id)


def dispatch_shadow(claim_id: uuid.UUID) -> None:
    """Advisory proposals run after the pipeline, in their own transaction; never retried."""
    if not get_settings().shadow_mode_enabled:
        return
    if get_settings().pipeline_mode == "celery":
        try:
            shadow_propose_task.delay(str(claim_id))
        except Exception:  # noqa: BLE001 - a missing proposal is acceptable; a lost claim is not
            log.exception("could not enqueue shadow proposal for claim %s", claim_id)
    else:
        run_shadow(claim_id)


def dispatch_rematch(org_id: uuid.UUID) -> bool:
    """Returns True when the rematch ran synchronously (its result is visible now)."""
    if get_settings().pipeline_mode == "celery":
        rematch_task.delay(str(org_id))
        return False
    run_rematch(org_id)
    return True
