"""FastAPI application — Meridian inference gateway.

Composition only: AppState lifecycle, auth middleware, thin route handlers.
Policy logic lives in ``pipeline``; routing in ``routing``; teardown in
``finalize``.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import generate_latest

from meridian.api.authz import require_key_admin, require_ops_view, require_reload
from meridian.api.errors import GatewayError
from meridian.api.finalize import finalize_request, stamp_meridian_headers
from meridian.api.keys_admin import create_key, delete_key, list_keys
from meridian.api.pipeline import ChatRequest, prepare_chat_request, reconcile_budget_usage
from meridian.api.reload import reload_config
from meridian.api.routing import route
from meridian.api.state import AppState, build_app_state, shutdown_app_state
from meridian.auth import AuthError, authenticate
from meridian.config.models import MeridianConfig
from meridian.cost.authz import clamp_window_days, require_usage_identity, resolve_usage_scope
from meridian.cost.extract import usage_from_dict, usage_from_sse_bytes
from meridian.cost.record import record_actual_usage
from meridian.metrics.collectors import (
    BACKEND_CIRCUIT_OPEN,
    BACKEND_HEALTHY,
    BACKEND_IDLE,
    BACKEND_INFLIGHT,
)
from meridian.proxy.forward import (
    StreamUpstreamError,
    close_client,
    forward_get,
    forward_non_stream,
    forward_stream,
)
from meridian.resilience import CircuitOpenError
from meridian.util.helpers import generate_request_id, now_ms


def _wants_usage_scrape(state: AppState, chat: ChatRequest) -> bool:
    """Scrape backend usage for cost ledger and/or budget reconcile."""
    return state.cost_ledger is not None or bool(chat.meter_keys)


def _apply_actual_usage(
    state: AppState,
    chat: ChatRequest,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    record_actual_usage(
        state,
        model=chat.model,
        org_id=chat.org_id,
        team_id=chat.team_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        key_id=chat.identity.key_id if chat.identity else None,
    )
    reconcile_budget_usage(
        state,
        meter_keys=chat.meter_keys,
        estimated_cost=chat.request_ctx.cost,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )

logger = logging.getLogger("meridian")

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
    loop = asyncio.get_running_loop()

    def _on_sighup() -> None:
        async def _do_reload() -> None:
            try:
                report = await reload_config(state)
                logger.info("SIGHUP: reload complete (%s)", report)
            except Exception:
                logger.exception("SIGHUP: config reload failed (state unchanged)")

        loop.create_task(_do_reload())

    try:
        loop.add_signal_handler(signal.SIGHUP, _on_sighup)
    except (NotImplementedError, RuntimeError):
        # Windows / restricted environments — POST /meridian/reload still works.
        logger.debug("SIGHUP handler not available on this platform")

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

    try:
        loop.remove_signal_handler(signal.SIGHUP)
    except (NotImplementedError, RuntimeError):
        pass
    for task in sweep_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await shutdown_app()


app = FastAPI(title="Meridian", version="0.11.0", lifespan=lifespan)


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
            return GatewayError(exc.message, exc.error_type, 401).to_response()
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
        state, chat.model, chat.request_ctx, session_id, org_id=chat.org_id
    )
    if backend is None:
        return GatewayError(
            f"No healthy backend available for model {chat.model!r}",
            "meridian_no_backend",
            503,
        ).to_response()

    backend.increment_inflight()
    backend.add_inflight_cost(chat.request_ctx.cost)
    backend.touch(start)
    BACKEND_INFLIGHT.labels(backend=backend.name).inc()

    status_code = 200
    error_type: Optional[str] = None
    is_stream = chat.is_stream

    try:
        if is_stream:
            resp = await forward_stream(backend, chat.body, state.config.resilience)
            original_body_iterator = resp.body_iterator

            async def tracked_stream() -> AsyncIterator[bytes]:
                nonlocal status_code, error_type
                # ponytail: keep last ~64KiB of SSE for usage scrape; don't buffer whole stream
                tail = bytearray()
                try:
                    async for chunk in original_body_iterator:
                        if isinstance(chunk, bytes):
                            raw = chunk
                        elif isinstance(chunk, str):
                            raw = chunk.encode()
                        else:
                            raw = bytes(chunk)
                        tail.extend(raw)
                        if len(tail) > 65536:
                            del tail[: len(tail) - 65536]
                        yield raw
                except StreamUpstreamError as exc:
                    # Client already got error event + [DONE]; account + end quietly.
                    error_type = exc.error_type
                    status_code = 504 if exc.error_type == "stream_read_timeout" else 502
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
                    if status_code < 400 and _wants_usage_scrape(state, chat):
                        found = usage_from_sse_bytes(bytes(tail))
                        if found is not None:
                            _apply_actual_usage(state, chat, found[0], found[1])
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
            stamp_meridian_headers(
                resp.headers,
                request_id=request_id,
                backend=backend.name,
                tier_name=tier_name,
                session_route=session_route,
                budget_remaining_tokens=chat.budget_remaining_tokens,
                budget_remaining_requests=chat.budget_remaining_requests,
            )
            return resp

        non_stream_resp = await forward_non_stream(
            backend, chat.body, state.config.resilience
        )
        status_code = non_stream_resp.status_code
        if status_code >= 500:
            state.health_checker.check_passive_failure(backend)
            error_type = "upstream_5xx"
        elif status_code < 400 and _wants_usage_scrape(state, chat):
            try:
                import json as _json
                found = usage_from_dict(_json.loads(bytes(non_stream_resp.body)))
            except Exception:
                found = None
            if found is not None:
                _apply_actual_usage(state, chat, found[0], found[1])
        stamp_meridian_headers(
            non_stream_resp.headers,
            request_id=request_id,
            backend=backend.name,
            tier_name=tier_name,
            session_route=session_route,
            budget_remaining_tokens=chat.budget_remaining_tokens,
            budget_remaining_requests=chat.budget_remaining_requests,
        )
        return non_stream_resp

    except CircuitOpenError:
        # Circuit breaker rejected pre-flight — no upstream call was made.
        error_type = "circuit_open"
        status_code = 503
        return GatewayError(
            f"Backend {backend.name!r} circuit is open",
            "meridian_circuit_open",
            503,
        ).to_response()
    except httpx.RequestError as exc:
        state.health_checker.check_passive_failure(backend)
        error_type = type(exc).__name__
        status_code = 502
        return GatewayError(
            f"Backend {backend.name!r} connection error",
            "meridian_backend_error",
            502,
        ).to_response()
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


def _status_body(state: AppState) -> dict[str, object]:
    body: dict[str, object] = {
        "strategy": state.config.gateway.strategy,
        "backends": [b.to_status_dict() for b in state.registry.all_backends()],
    }
    if state.canary is not None:
        body["canary"] = state.canary.status()
    return body


@app.get("/meridian/status")
async def status(request: Request) -> Response:
    state = get_state()
    try:
        require_ops_view(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
    except GatewayError as exc:
        return exc.to_response()
    return JSONResponse(_status_body(state))


@app.get("/meridian/version")
async def version() -> JSONResponse:
    """Package / API version for ops smoke checks (no secrets)."""
    from meridian import __version__ as pkg_version

    return JSONResponse({
        "name": "meridian",
        "version": pkg_version,
        "api": "0.9.1",
    })


@app.get("/meridian/requests")
async def recent_requests(request: Request) -> Response:
    state = get_state()
    try:
        require_ops_view(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
    except GatewayError as exc:
        return exc.to_response()
    return JSONResponse({"requests": list(state.recent_requests)})


@app.post("/meridian/reload")
async def reload_auth_keys(request: Request) -> Response:
    """Hot-reload config (full when a config file is known, else keys only).

    Requires auth.enabled and a key with reload rights (ops_admin, or role
    operator/admin). Atomic: an invalid new config is rejected and the running
    state is kept. In-flight requests keep their already-resolved objects.
    """
    state = get_state()
    try:
        require_reload(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
    except GatewayError as exc:
        return exc.to_response()
    try:
        report = await reload_config(state)
    except Exception as exc:
        logger.exception("Config reload failed")
        return GatewayError(
            f"Config reload failed: {exc}",
            "invalid_request_error",
            400,
        ).to_response()
    return JSONResponse({"reloaded": True, **report})


@app.get("/meridian/keys")
async def keys_list(request: Request) -> Response:
    """List API keys, redacted (id/org/role/source only — never the raw key).

    Requires auth.enabled and a key_admin-capable key (ops_admin or role admin).
    """
    state = get_state()
    try:
        require_key_admin(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
        return JSONResponse({"keys": list_keys(state)})
    except GatewayError as exc:
        return exc.to_response()
    except Exception as exc:
        logger.exception("Key listing failed")
        return GatewayError(str(exc), "meridian_internal_error", 500).to_response()


@app.post("/meridian/keys", status_code=201)
async def keys_create(request: Request) -> Response:
    """Create an API key, persist to keys_file, hot-swap the index.

    The full key value is returned exactly once, in this response. ``key``
    may be provided explicitly (must match the mrdn_ pattern) or generated.
    """
    from meridian.api.keys_admin import KeyCreateRequest

    state = get_state()
    try:
        require_key_admin(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
    except GatewayError as exc:
        return exc.to_response()
    try:
        body = await request.json()
        req = KeyCreateRequest.model_validate(body)
    except Exception:
        return GatewayError(
            "Invalid request body — expected KeyCreateRequest fields",
            "invalid_request_error",
            400,
        ).to_response()
    try:
        view, full_key = create_key(
            state,
            org_id=req.org_id,
            key=req.key,
            team_id=req.team_id,
            user_id=req.user_id,
            key_id=req.key_id,
            allowed_models=req.allowed_models,
            pii_policy=req.pii_policy,
            cost_admin=req.cost_admin,
            ops_admin=req.ops_admin,
            role=req.role,
        )
        return JSONResponse({"key": full_key, **view}, status_code=201)
    except GatewayError as exc:
        return exc.to_response()
    except Exception as exc:
        logger.exception("Key creation failed")
        return GatewayError(str(exc), "meridian_internal_error", 500).to_response()


@app.delete("/meridian/keys/{key_id}")
async def keys_delete(key_id: str, request: Request) -> Response:
    """Delete a keys_file key by its non-secret id. Inline-config keys refused.

    Guards: you may not delete the key making the request, nor the last
    remaining key-admin key.
    """
    state = get_state()
    try:
        identity = require_key_admin(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
        view = delete_key(
            state, key_id, caller_key_id=identity.key_id if identity else None
        )
        return JSONResponse({"deleted": True, **view})
    except GatewayError as exc:
        return exc.to_response()
    except Exception as exc:
        logger.exception("Key deletion failed")
        return GatewayError(str(exc), "meridian_internal_error", 500).to_response()


@app.get("/meridian/usage")
async def usage_report(
    request: Request,
    org: Optional[str] = None,
    team: Optional[str] = None,
    key: Optional[str] = None,
    window_days: int = 30,
) -> Response:
    """Cost/token report. Requires auth when cost is enabled; org-scoped by key.

    ``key`` filters rows by the non-secret key_id (Phase 3 per-key tracking);
    the org/team clamp from the caller's own key still applies.
    """
    state = get_state()
    if state.cost_ledger is None:
        return JSONResponse({
            "enabled": False,
            "currency": state.config.cost.currency,
            "rows": [],
        })
    try:
        identity = require_usage_identity(
            auth_enabled=state.config.auth.enabled,
            key_index=state.key_index,
            authorization=request.headers.get("authorization"),
        )
        org_f, team_f = resolve_usage_scope(identity, org, team)
    except GatewayError as exc:
        return exc.to_response()
    window = clamp_window_days(window_days, state.config.cost.max_window_days)
    rows = state.cost_ledger.query(
        org_id=org_f, team_id=team_f, key_id=key, window_days=window
    )
    return JSONResponse({
        "enabled": True,
        "currency": state.config.cost.currency,
        "rows": [
            {
                "org_id": r.org_id,
                "team_id": r.team_id,
                "model": r.model,
                "day": r.day,
                "key_id": r.key_id,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "requests": r.requests,
                "cost": round(r.cost, 6),
            }
            for r in rows
        ],
    })


@app.get("/meridian/usage.csv")
async def usage_csv(
    request: Request,
    org: Optional[str] = None,
    team: Optional[str] = None,
    key: Optional[str] = None,
    window_days: int = 30,
) -> Response:
    import csv
    import io

    state = get_state()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "org_id", "team_id", "model", "day", "key_id",
        "prompt_tokens", "completion_tokens", "requests", "cost", "currency",
    ])
    if state.cost_ledger is not None:
        try:
            identity = require_usage_identity(
                auth_enabled=state.config.auth.enabled,
                key_index=state.key_index,
                authorization=request.headers.get("authorization"),
            )
            org_f, team_f = resolve_usage_scope(identity, org, team)
        except GatewayError as exc:
            return exc.to_response()
        window = clamp_window_days(window_days, state.config.cost.max_window_days)
        for r in state.cost_ledger.query(
            org_id=org_f, team_id=team_f, key_id=key, window_days=window
        ):
            w.writerow([
                r.org_id, r.team_id, r.model, r.day, r.key_id,
                r.prompt_tokens, r.completion_tokens, r.requests,
                f"{r.cost:.6f}", state.config.cost.currency,
            ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=meridian_usage.csv"},
    )


@app.get("/metrics")
async def metrics() -> Response:
    state = _state
    if state is not None:
        for b in state.registry.all_backends():
            BACKEND_HEALTHY.labels(backend=b.name).set(1 if b.healthy else 0)
            BACKEND_IDLE.labels(backend=b.name).set(1 if b.idle else 0)
            if b.circuit is not None:
                BACKEND_CIRCUIT_OPEN.labels(backend=b.name).set(
                    1 if b.circuit.state == "open" else 0
                )
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


_UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@app.get("/ui")
async def ui() -> FileResponse:
    return FileResponse(_UI_DIR / "index.html", media_type="text/html")
