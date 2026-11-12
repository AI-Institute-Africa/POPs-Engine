"""The /demo endpoints: scenario runner, sample files for the try-it buttons, and reset.

Only mounted when POPS_DEMO=true in a dev environment (see app.main). No authentication: the demo
binds to localhost by default, and everything it touches is synthetic.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import get_settings
from app.demo import scenarios as S
from app.demo import service

router = APIRouter(prefix="/demo", tags=["demo"])


def _api(request: Request) -> S.Api:
    # the product's own app, in-process: the scenario acts through the same API as any client
    return service.api_for(
        TestClient(request.app, base_url="http://demo.local", raise_server_exceptions=False)
    )


def _result(r: S.Result) -> dict[str, Any]:
    return {
        "scenario": r.scenario,
        "kind": r.kind,
        "ref_id": r.ref_id,
        "state": r.state,
        "headline": r.headline,
        "reasons": r.reasons,
        "followups": r.followups,
        "payer_messages": r.payer_messages,
        "steps": [{"who": s.who, "text": s.text} for s in r.steps],
    }


@router.get("/info")
def info() -> dict[str, Any]:
    s = get_settings()
    return {
        "demo": True,
        "org": S.DEMO_ORG,
        "personas": [
            {"key": k, "email": e, "role": r, "label": label}
            for k, (e, r, label) in S.PERSONAS.items()
        ],
        "scenarios": [
            {
                "id": sc.id,
                "group": sc.group,
                "title": sc.title,
                "pitch": sc.pitch,
                "learn": sc.learn,
                "outcome": sc.outcome,
            }
            for sc in S.SCENARIOS
        ],
        "simulated": {
            "readers": s.vlm_backend == "fake",
            "face": s.kyc_face_backend in ("demo", "fake"),
        },
    }


@router.post("/scenarios/{scenario_id}/run")
def run(scenario_id: str, request: Request) -> dict[str, Any]:
    sc = S.BY_ID.get(scenario_id)
    if sc is None:
        raise HTTPException(404, "unknown scenario")
    try:
        return _result(sc.run(_api(request)))
    except S.DemoError as exc:
        raise HTTPException(502, str(exc)) from exc


class MoneyIn(BaseModel):
    claim_id: str
    txn: str
    amount: str


@router.post("/followups/deliver-money")
def deliver_money(body: MoneyIn, request: Request) -> dict[str, Any]:
    try:
        return _result(
            S.deliver_money(_api(request), claim_id=body.claim_id, txn=body.txn, amount=body.amount)
        )
    except S.DemoError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/prepare/payment/{variant}")
def prepare_payment(variant: str, request: Request) -> dict[str, Any]:
    """An invoice and a receipt file to submit by hand (and the bank record, where the story has one)."""
    if variant not in S.PAYMENT_VARIANTS:
        raise HTTPException(404, "unknown variant")
    s = S.payment_sample(_api(request), variant)
    key = service.FILES.put(f"{uuid.uuid4().hex}.png", s.png)
    return {
        "variant": variant,
        "note": s.note,
        "payment_code": s.invoice["payment_code"],
        "claimant_ref": s.invoice["learner_ref"],
        "amount": s.amount,
        "currency": "USD",
        "channel": "ecocash",
        "file_url": f"/demo/files/{key}",
        "filename": "receipt.png",
    }


@router.post("/prepare/kyc/{variant}")
def prepare_kyc(variant: str) -> dict[str, Any]:
    """An ID photo and a selfie to submit by hand."""
    if variant not in S.KYC_VARIANTS:
        raise HTTPException(404, "unknown variant")
    s = S.kyc_sample(variant)
    return {
        "variant": variant,
        "note": s.note,
        "subject_ref": f"learner-{uuid.uuid4().hex[:6].upper()}",
        "front_url": f"/demo/files/{service.FILES.put(uuid.uuid4().hex + '.png', s.front)}",
        "selfie_url": f"/demo/files/{service.FILES.put(uuid.uuid4().hex + '.png', s.selfie)}",
    }


@router.get("/files/{key}")
def file(key: str) -> Response:
    data = service.FILES.get(key)
    if data is None:
        raise HTTPException(404, "that sample has expired; create a new one")
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/reset")
def reset(request: Request) -> dict[str, Any]:
    """Wipe everything and start again with the seeded demo data."""
    service.wipe()
    return {"seeded": service.seed(_api(request))}
