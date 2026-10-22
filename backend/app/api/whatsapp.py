"""WhatsApp Business Cloud API webhook (configure its URL as <api>/whatsapp/webhook in Meta)."""

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session
from app.whatsapp import service as wa
from app.workers.tasks import dispatch_pipeline

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
DB = Annotated[Session, Depends(get_session)]


def _enabled() -> None:
    s = get_settings()
    if not (s.whatsapp_verify_token and s.whatsapp_app_secret and s.whatsapp_access_token):
        raise HTTPException(404, "WhatsApp intake is not configured")


@router.get("/webhook", response_class=PlainTextResponse)
def verify(
    mode: Annotated[str, Query(alias="hub.mode")],
    token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    """Meta's one-time subscription handshake."""
    _enabled()
    if mode != "subscribe" or token != get_settings().whatsapp_verify_token:
        raise HTTPException(403, "verify token mismatch")
    return challenge


@router.post("/webhook")
async def receive(request: Request, db: DB, background: BackgroundTasks) -> dict:
    _enabled()
    body = await request.body()
    if not wa.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "body is not JSON") from exc
    outcomes = []
    for msg in wa.messages(payload):
        outcome = wa.handle_message(db, msg)
        outcomes.append((str(msg.get("from", "")), outcome))
    db.commit()
    for to, outcome in outcomes:
        if outcome.claim_id:
            dispatch_pipeline(outcome.claim_id)
        if outcome.reply and to:
            background.add_task(wa.send_reply, to, outcome.reply)
    return {"received": len(outcomes), "claims": sum(1 for _, o in outcomes if o.claim_id)}
