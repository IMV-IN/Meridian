"""FastAPI routes for the node control protocol (contracts/v1) plus operator
admin endpoints. Sync handlers run in FastAPI's threadpool, matching the sync
SQLAlchemy service."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse

from .service import ControlService, ControlServiceError


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _svc(request: Request) -> ControlService:
    return cast(ControlService, request.app.state.control_service)


router = APIRouter()


# --- node protocol ------------------------------------------------------
@router.post("/control/v1/enroll")
def enroll(request: Request, body: dict = Body(...), authorization: Optional[str] = Header(None)):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    return _svc(request).enroll(token, body)


@router.get("/control/v1/enroll/claims/{claim_id}")
def get_claim(request: Request, claim_id: str, x_possession_proof: Optional[str] = Header(None)):
    svc = _svc(request)
    if x_possession_proof is None:
        return {"status": "pending", "claim_id": claim_id, "nonce": svc.claim_nonce(claim_id)}
    return svc.resolve_claim(claim_id, _b64url_decode(x_possession_proof))


def _client_cert(request: Request) -> Optional[str]:
    return request.headers.get(_svc(request)._config.client_cert_header)


@router.post("/control/v1/nodes/{node_id}/sessions")
def establish_session(request: Request, node_id: str, body: dict = Body(...)):
    svc = _svc(request)
    svc.verify_node_identity(node_id, _client_cert(request))
    return svc.establish_session(node_id, body)


@router.post("/control/v1/nodes/{node_id}/heartbeat")
def heartbeat(request: Request, node_id: str, body: dict = Body(...)):
    svc = _svc(request)
    svc.verify_node_identity(node_id, _client_cert(request))
    return svc.heartbeat(node_id, body)


@router.get("/control/v1/nodes/{node_id}/desired-state")
def desired_state(request: Request, node_id: str, generation: int = Query(0)):
    svc = _svc(request)
    svc.verify_node_identity(node_id, _client_cert(request))
    return svc.fetch_desired_state(node_id, generation)


@router.post("/control/v1/nodes/{node_id}/observations")
def observations(request: Request, node_id: str, body: dict = Body(...)):
    svc = _svc(request)
    svc.verify_node_identity(node_id, _client_cert(request))
    return svc.post_observation(node_id, body)


@router.post("/control/v1/nodes/{node_id}/certificate")
def rotate_certificate(request: Request, node_id: str):
    svc = _svc(request)
    svc.verify_node_identity(node_id, _client_cert(request))
    return svc.rotate_certificate(node_id)


# --- operator admin -----------------------------------------------------
@router.post("/admin/tokens")
def create_token(request: Request, body: dict = Body(default={})):
    token = _svc(request).create_token(
        auto_approve=bool(body.get("auto_approve", False)),
        ttl_seconds=int(body.get("ttl_seconds", 3600)),
    )
    return {"token": token}


@router.post("/admin/claims/{claim_id}/approve")
def approve_claim(request: Request, claim_id: str):
    _svc(request).approve_claim(claim_id)
    return {"ok": True}


@router.post("/admin/claims/{claim_id}/deny")
def deny_claim(request: Request, claim_id: str):
    _svc(request).deny_claim(claim_id)
    return {"ok": True}


@router.post("/admin/nodes/{node_id}/desired")
def set_desired(request: Request, node_id: str, body: dict = Body(...)):
    _svc(request).set_desired(node_id, body)
    return {"ok": True}


@router.post("/admin/nodes/{node_id}/stop-authorize")
def stop_authorize(request: Request, node_id: str, body: dict = Body(...)):
    _svc(request).authorize_stop(node_id, list(body.get("engine_ids", [])))
    return {"ok": True}


@router.post("/admin/nodes/{node_id}/revoke")
def revoke(request: Request, node_id: str):
    _svc(request).revoke_node(node_id)
    return {"ok": True}


@router.post("/admin/restore")
def restore(request: Request, body: dict = Body(...)):
    incarnation = _svc(request).record_restore(int(body.get("high_water_epoch", 0)))
    return {"incarnation": incarnation}


@router.get("/admin/projection")
def projection(request: Request):
    return {"endpoints": _svc(request).serving_projection()}


@router.post("/admin/place")
def place(request: Request, body: dict = Body(...)):
    placement = _svc(request).select_placement(
        int(body.get("required_vram_bytes", 0)),
        count=int(body.get("count", 1)),
        artifact_digest=body.get("artifact_digest"),
    )
    return {"placement": placement}


def register_error_handler(app) -> None:
    @app.exception_handler(ControlServiceError)
    async def _handle(request: Request, exc: ControlServiceError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {
                "code": exc.code, "message": exc.message, "retryable": exc.retryable,
                "server_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }},
        )
