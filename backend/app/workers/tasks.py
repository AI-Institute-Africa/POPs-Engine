"""Background jobs. Every task is idempotent and safe to retry."""

import logging
import uuid

from celery import Celery

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
    },
)


def run_pipeline(claim_id: uuid.UUID) -> None:
    from app.workers.pipeline import process_claim

    with session_scope() as session:
        process_claim(session, claim_id)


def run_rematch(org_id: uuid.UUID) -> int:
    from app.workers.pipeline import rematch_awaiting

    with session_scope() as session:
        return rematch_awaiting(session, org_id)


@celery_app.task(
    name="pops.process_claim", autoretry_for=(Exception,), max_retries=3, retry_backoff=True
)
def process_claim_task(claim_id: str) -> None:
    run_pipeline(uuid.UUID(claim_id))


@celery_app.task(name="pops.rematch", autoretry_for=(Exception,), max_retries=3)
def rematch_task(org_id: str) -> int:
    return run_rematch(uuid.UUID(org_id))


@celery_app.task(name="pops.deliver_outbox")
def deliver_outbox_task() -> int:
    from app.entitlements.outbox import deliver_pending

    with session_scope() as session:
        return deliver_pending(session)


def dispatch_pipeline(claim_id: uuid.UUID) -> None:
    if get_settings().pipeline_mode == "celery":
        process_claim_task.delay(str(claim_id))
    else:
        run_pipeline(claim_id)


def dispatch_rematch(org_id: uuid.UUID) -> None:
    if get_settings().pipeline_mode == "celery":
        rematch_task.delay(str(org_id))
    else:
        run_rematch(org_id)
