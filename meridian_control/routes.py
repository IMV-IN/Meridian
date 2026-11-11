"""FastAPI routes for the node control protocol (contracts/v1) plus operator
admin endpoints. Sync handlers run in FastAPI's threadpool, matching the sync
SQLAlchemy service."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .service import ControlService, ControlServiceError


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _svc(request: Request) -> ControlService:
    return cast(ControlService, request.app.state.control_service)


router = APIRouter()
# Unauthenticated operator API. Standalone deployments rely on network
# placement; cloud mode disables it (tenants use the session-authenticated
# /api/w/{workspace}/* routes instead).
admin_router = APIRouter()


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
@admin_router.post("/admin/tokens")
def create_token(request: Request, body: dict = Body(default={})):
    token = _svc(request).create_token(
        auto_approve=bool(body.get("auto_approve", False)),
        ttl_seconds=int(body.get("ttl_seconds", 3600)),
    )
    return {"token": token}


@admin_router.post("/admin/claims/{claim_id}/approve")
def approve_claim(request: Request, claim_id: str):
    _svc(request).approve_claim(claim_id)
    return {"ok": True}


@admin_router.post("/admin/claims/{claim_id}/deny")
def deny_claim(request: Request, claim_id: str):
    _svc(request).deny_claim(claim_id)
    return {"ok": True}


@admin_router.post("/admin/nodes/{node_id}/desired")
def set_desired(request: Request, node_id: str, body: dict = Body(...)):
    _svc(request).set_desired(node_id, body)
    return {"ok": True}


@admin_router.post("/admin/nodes/{node_id}/stop-authorize")
def stop_authorize(request: Request, node_id: str, body: dict = Body(...)):
    _svc(request).authorize_stop(node_id, list(body.get("engine_ids", [])))
    return {"ok": True}


@admin_router.post("/admin/nodes/{node_id}/revoke")
def revoke(request: Request, node_id: str):
    _svc(request).revoke_node(node_id)
    return {"ok": True}


@admin_router.post("/admin/restore")
def restore(request: Request, body: dict = Body(...)):
    incarnation = _svc(request).record_restore(int(body.get("high_water_epoch", 0)))
    return {"incarnation": incarnation}


@admin_router.get("/admin/projection")
def projection(request: Request):
    return {"endpoints": _svc(request).serving_projection()}


@admin_router.get("/admin/crl")
def crl(request: Request):
    return {"revoked": _svc(request).crl()}


# --- operator console read models (read-only) ---------------------------
@admin_router.get("/admin/overview")
def overview(request: Request):
    return _svc(request).overview()


@admin_router.get("/admin/nodes")
def list_nodes(request: Request):
    return {"nodes": _svc(request).list_nodes()}


@admin_router.get("/admin/nodes/{node_id}")
def node_detail(request: Request, node_id: str):
    return _svc(request).node_detail(node_id)


@admin_router.get("/admin/claims")
def list_claims(request: Request, status: Optional[str] = Query(None)):
    return {"claims": _svc(request).list_claims(status)}


@admin_router.get("/admin/tokens")
def list_tokens(request: Request):
    return {"tokens": _svc(request).list_tokens()}


@admin_router.get("/admin/audit")
def list_audit(request: Request, limit: int = Query(100), node_id: Optional[str] = Query(None)):
    return {"events": _svc(request).list_audit(limit=limit, node_id=node_id)}


@admin_router.post("/admin/place")
def place(request: Request, body: dict = Body(...)):
    placement = _svc(request).select_placement(
        int(body.get("required_vram_bytes", 0)),
        count=int(body.get("count", 1)),
        artifact_digest=body.get("artifact_digest"),
    )
    return {"placement": placement}


def error_response(exc: ControlServiceError) -> JSONResponse:
    """The contracts/v1 error envelope."""
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {
            "code": exc.code, "message": exc.message, "retryable": exc.retryable,
            "server_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }},
    )


class BodyTooLarge(HTTPException):
    """Raised while a request body streams in past its route's limit. An
    HTTPException, so FastAPI's body parsing passes it through untouched."""

    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413)
        self.error = ControlServiceError("TOO_LARGE", f"the request body is larger than {limit} bytes", http_status=413)


class BodyLimit:
    """ASGI middleware: reject a request body over its route's limit with 413.
    Checks Content-Length up front and counts the bytes as they stream in, so a
    chunked body without a length is cut off too, before anything parses it."""

    UNAUTHENTICATED = ("/auth/", "/oauth/", "/api/public/", "/control/v1/enroll")

    def __init__(self, app, config) -> None:
        self.app = app
        self.config = config

    def limit_for(self, path: str) -> int:
        c = self.config
        if path.startswith("/control/v1/nodes/") and path.endswith("/observations"):
            return int(c.max_observation_bytes)
        if path.startswith(self.UNAUTHENTICATED):
            return int(c.max_unauthenticated_body_bytes)
        return int(c.max_body_bytes)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await error_response(BodyTooLarge(limit).error)(scope, receive, send)
            return
        received = 0
        started = False

        async def counted():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        async def tracked(message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except BodyTooLarge as e:  # read outside FastAPI's handlers (a raw ASGI read)
            if not started:
                await error_response(e.error)(scope, receive, send)


def register_error_handler(app) -> None:
    @app.exception_handler(ControlServiceError)
    async def _handle(request: Request, exc: ControlServiceError):
        return error_response(exc)

    @app.exception_handler(BodyTooLarge)
    async def _too_large(request: Request, exc: BodyTooLarge):
        return error_response(exc.error)
