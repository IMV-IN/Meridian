"""FastAPI application — Meridian inference gateway.

Composition only: AppState lifecycle, auth middleware, thin route handlers.
Policy logic lives in ``pipeline``; routing in ``routing``; teardown in
``finalize``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import generate_latest

from meridian.api.errors import GatewayError, error_json
from meridian.api.finalize import finalize_request
from meridian.api.pipeline import prepare_chat_request
from meridian.api.routing import route
from meridian.api.state import AppState, build_app_state, shutdown_app_state
from meridian.auth import AuthError, authenticate
from meridian.config.models import MeridianConfig
from meridian.metrics.collectors import BACKEND_HEALTHY
from meridian.proxy.forward import close_client, forward_get, forward_non_stream, forward_stream
from meridian.util.helpers import generate_request_id, now_ms

logger = logging.getLogger("meridian")

# Process-wide state (set by init_app). Prefer get_state() over reaching in.
_state: Optional[AppState] = None


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("App state not initialized; call init_app() first")
    return _state


async def init_app(
    config: Optional[MeridianConfig] = None,
    start_health: bool = True,
) -> AppState:
    """Initialize gateway state. Used by lifespan and tests."""
    global _state
    _state = await build_app_state(config, start_background=start_health)
    return _state


async def shutdown_app() -> None:
    global _state
    if _state is not None:
        await shutdown_app_state(_state)
        _state = None
    await close_client()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    state = await init_app()
    app.state.meridian = state

    sweep_tasks: list[asyncio.Task[None]] = []
    if state.session_store is not None:
        async def _affinity_sweep() -> None:
            while True:
                await asyncio.sleep(state.config.session_affinity.sweep_interval_s)
                state.session_store.sweep()  # type: ignore[union-attr]
        sweep_tasks.append(asyncio.create_task(_affinity_sweep()))

    if state.config.rate_limit.enabled:
        async def _rl_sweep() -> None:
            while True:
                await asyncio.sleep(state.config.rate_limit.sweep_interval_s)
                state.rate_limit.sweep()
        sweep_tasks.append(asyncio.create_task(_rl_sweep()))

    yield

    for task in sweep_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await shutdown_app()


app = FastAPI(title="Meridian", version="0.7.0", lifespan=lifespan)


@app.middleware("http")
async def _auth_gate(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    state = _state
    if state is not None and state.config.auth.enabled and request.url.path.startswith("/v1/"):
        try:
            request.state.identity = authenticate(
                request.headers.get("authorization"), state.key_index
            )
        except AuthError as exc:
            return error_json(exc.message, exc.error_type, 401)
    return await call_next(request)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    state = get_state()
    request_id = generate_request_id()
    start = now_ms()

    try:
        chat = await prepare_chat_request(
            state, request, request_id=request_id, start_ms=start
        )
    except GatewayError as exc:
        return exc.to_response()

    session_id = None
    if state.config.session_affinity.enabled:
        session_id = request.headers.get(state.config.session_affinity.header)

    backend, tier_name, session_route = route(
        state, chat.model, chat.request_ctx, session_id
    )
    if backend is None:
        return error_json(
            f"No healthy backend available for model {chat.model!r}",
            "meridian_no_backend",
            503,
        )

    backend.increment_inflight()
    backend.add_inflight_cost(chat.request_ctx.cost)
    from meridian.metrics.collectors import BACKEND_INFLIGHT
    BACKEND_INFLIGHT.labels(backend=backend.name).inc()

    status_code = 200
    error_type: Optional[str] = None
    is_stream = chat.is_stream

    try:
        if is_stream:
            resp = await forward_stream(backend, chat.body)
            original_body_iterator = resp.body_iterator

            async def tracked_stream() -> AsyncIterator[bytes]:
                nonlocal status_code, error_type
                try:
                    async for chunk in original_body_iterator:
                        if isinstance(chunk, bytes):
                            yield chunk
                        elif isinstance(chunk, str):
                            yield chunk.encode()
                        else:
                            yield bytes(chunk)
                except httpx.RequestError as exc:
                    state.health_checker.check_passive_failure(backend)
                    error_type = type(exc).__name__
                    status_code = 502
                    raise
                except asyncio.CancelledError:
                    error_type = error_type or "client_disconnect"
                    status_code = 499
                    raise
                finally:
                    finalize_request(
                        state,
                        request_id=request_id,
                        model=chat.model,
                        stream=True,
                        backend=backend,
                        status_code=status_code,
                        start=start,
                        error_type=error_type,
                        request_ctx=chat.request_ctx,
                        tier_name=tier_name,
                        session_route=session_route,
                        org_id=chat.org_id,
                        team_id=chat.team_id,
                        pii_counts=chat.pii_counts,
                    )

            resp.body_iterator = tracked_stream()
            resp.headers["x-request-id"] = request_id
            resp.headers["x-meridian-backend"] = backend.name
            if tier_name is not None:
                resp.headers["x-meridian-tier"] = tier_name
            if session_route is not None:
                resp.headers["x-meridian-session-route"] = session_route
            return resp

        non_stream_resp = await forward_non_stream(backend, chat.body)
        status_code = non_stream_resp.status_code
        if status_code >= 500:
            state.health_checker.check_passive_failure(backend)
            error_type = "upstream_5xx"
        non_stream_resp.headers["x-request-id"] = request_id
        non_stream_resp.headers["x-meridian-backend"] = backend.name
        if tier_name is not None:
            non_stream_resp.headers["x-meridian-tier"] = tier_name
        if session_route is not None:
            non_stream_resp.headers["x-meridian-session-route"] = session_route
        return non_stream_resp

    except httpx.RequestError as exc:
        state.health_checker.check_passive_failure(backend)
        error_type = type(exc).__name__
        status_code = 502
        return error_json(
            f"Backend {backend.name!r} connection error: {exc}",
            "meridian_backend_error",
            502,
        )
    finally:
        if not is_stream:
            finalize_request(
                state,
                request_id=request_id,
                model=chat.model,
                stream=False,
                backend=backend,
                status_code=status_code,
                start=start,
                error_type=error_type,
                request_ctx=chat.request_ctx,
                tier_name=tier_name,
                session_route=session_route,
                org_id=chat.org_id,
                team_id=chat.team_id,
                pii_counts=chat.pii_counts,
            )


@app.get("/v1/models")
async def list_models() -> Response:
    state = get_state()
    backends = state.registry.all_backends()
    if backends:
        for b in backends:
            if b.healthy:
                try:
                    return await forward_get(b, "/v1/models")
                except httpx.RequestError:
                    continue
    models = list({b.model for b in backends if b.model})
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "meridian"} for m in sorted(models)
        ],
    })


@app.get("/meridian/status")
async def status() -> JSONResponse:
    state = get_state()
    return JSONResponse({
        "strategy": state.config.gateway.strategy,
        "backends": [b.to_status_dict() for b in state.registry.all_backends()],
    })


@app.get("/meridian/requests")
async def recent_requests() -> JSONResponse:
    return JSONResponse({"requests": list(get_state().recent_requests)})


@app.get("/metrics")
async def metrics() -> Response:
    state = _state
    if state is not None:
        for b in state.registry.all_backends():
            BACKEND_HEALTHY.labels(backend=b.name).set(1 if b.healthy else 0)
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


_UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@app.get("/ui")
async def ui() -> FileResponse:
    return FileResponse(_UI_DIR / "index.html", media_type="text/html")


# ── Test / compat shims ─────────────────────────────────────────────────────
# Prefer get_state().rate_limit / get_state().registry. These aliases keep
# existing tests working with minimal churn.


class _StateProxy:
    """Attribute access forwards to the live AppState field of the same name."""

    def __init__(self, field: str) -> None:
        self._field = field

    def _obj(self):  # type: ignore[no-untyped-def]
        return getattr(get_state(), self._field)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._obj(), name)

    def clear(self) -> None:
        self._obj().clear()


# Populated for ``from meridian.api.main import _rate_limit`` style tests.
# After init_app, these proxy into get_state().
_rate_limit = _StateProxy("rate_limit")  # type: ignore[assignment]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """Lazy compat for ``_registry``, ``_finalize_request``, etc."""
    if name == "_registry":
        return get_state().registry
    if name == "_request_logger":
        return get_state().request_logger
    if name == "_audit_publisher":
        return get_state().audit_publisher
    if name == "_config":
        return get_state().config
    if name == "_finalize_request":
        # Adapt old signature used by tests (no state arg)
        def _wrap(**kwargs):  # type: ignore[no-untyped-def]
            return finalize_request(get_state(), **kwargs)
        return _wrap
    raise AttributeError(name)
