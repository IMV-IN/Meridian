"""FastAPI application — Meridian inference gateway."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, Dict, Optional, Set, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import generate_latest

from meridian.api.ratelimitter import RateLimitStore
from meridian.audit.publisher import AuditEvent, AuditEventPublisher
from meridian.auth import AuthError, IdentityContext, authenticate, build_key_index
from meridian.config.models import MeridianConfig
from meridian.health.checker import HealthChecker
from meridian.metrics.collectors import (
    BACKEND_HEALTHY,
    BACKEND_INFLIGHT,
    BUDGET_REJECTIONS,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)
from meridian.metrics.logger import RequestLogger
from meridian.proxy.forward import close_client, forward_get, forward_non_stream, forward_stream
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.affinity import SessionStore
from meridian.router.strategies import RequestContext, RoutingStrategy, create_strategy
from meridian.router.tiering import derive_tier
from meridian.router.token_estimator import estimate_prompt_tokens, extract_max_tokens
from meridian.telemetry import JsonTelemetryAdapter, TelemetryAdapter, TelemetryPoller
from meridian.usage import (
    InMemoryUsageMeter,
    SqliteUsageMeter,
    UsageMeter,
    build_meter_keys,
)
from meridian.util.helpers import generate_request_id, now_ms

logger = logging.getLogger("meridian")

# Module-level state (set during lifespan or init_app)
_registry: Optional[BackendRegistry] = None
_strategy: Optional[RoutingStrategy] = None
_health_checker: Optional[HealthChecker] = None
_telemetry_poller: Optional[TelemetryPoller] = None
_request_logger: Optional[RequestLogger] = None
_audit_publisher: Optional[AuditEventPublisher] = None
_config: Optional[MeridianConfig] = None
_session_store: Optional[SessionStore] = None
_key_index: Dict[str, IdentityContext] = {}
_usage_meter: Optional[UsageMeter] = None

# Rate limiting — bounded store (Milestone K); recreated in init_app.
_rate_limit: RateLimitStore = RateLimitStore()

# In-memory ring buffer for recent requests (serves the UI dashboard)
_recent_requests: deque[Dict[str, Any]] = deque(maxlen=100)


def _record_request(
    request_id: str, model: str, stream: bool, backend: str,
    status_code: int, latency_ms: float, error_type: Optional[str],
) -> None:
    _recent_requests.appendleft({
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "stream": stream,
        "chosen_backend": backend,
        "status_code": status_code,
        "latency_ms": round(latency_ms, 2),
        "error_type": error_type,
    })


def _load_config() -> MeridianConfig:
    path = os.environ.get("MERIDIAN_CONFIG", "config.yaml")
    if os.path.exists(path):
        return MeridianConfig.from_yaml(path)
    return MeridianConfig()


async def init_app(config: Optional[MeridianConfig] = None, start_health: bool = True) -> None:
    """Initialize app state. Called by lifespan or directly in tests."""
    global _registry, _strategy, _health_checker, _telemetry_poller, _request_logger, _audit_publisher, _config
    global _session_store, _key_index, _usage_meter, _rate_limit

    _config = config or _load_config()
    _key_index = build_key_index(_config.auth)
    if _config.auth.enabled:
        logger.info("API-key auth enabled — %d key(s) loaded", len(_key_index))

    _rate_limit = RateLimitStore(
        max_keys=_config.rate_limit.max_buckets,
        idle_ttl_s=_config.rate_limit.idle_ttl_s,
    )

    # Tenant budgets (Milestone J). Disabled by default; no-op when off.
    _usage_meter = None
    if _config.budgets.enabled:
        if _config.budgets.store == "memory":
            _usage_meter = InMemoryUsageMeter()
            logger.info("Tenant budgets enabled — in-memory store")
        else:
            _usage_meter = SqliteUsageMeter(_config.budgets.sqlite_path)
            logger.info(
                "Tenant budgets enabled — sqlite store at %s",
                _config.budgets.sqlite_path,
            )

    logging.basicConfig(
        level=getattr(logging, _config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    backends = [Backend(bc) for bc in _config.backends]
    _registry = BackendRegistry(backends)
    logger.info("Loaded %d backend(s): %s", len(backends), [b.name for b in backends])

    _strategy = create_strategy(
        _config.gateway.strategy,
        prefill_weight=_config.gateway.prefill_weight,
        decode_weight=_config.gateway.decode_weight,
        default_max_tokens=_config.gateway.default_max_tokens,
        queue_weight=_config.gateway.queue_weight,
        mem_weight=_config.gateway.mem_weight,
    )
    logger.info("Routing strategy: %s", _config.gateway.strategy)

    for b in backends:
        BACKEND_HEALTHY.labels(backend=b.name).set(1)
        BACKEND_INFLIGHT.labels(backend=b.name).set(0)

    _health_checker = HealthChecker(_registry, _config.health)
    if start_health:
        await _health_checker.start()

    # Telemetry adapters: one per backend that opted in via its config block.
    # Failures here are isolated per backend and do not affect health.
    adapters: Dict[str, TelemetryAdapter] = {}
    poll_interval = 5.0
    for bc in _config.backends:
        if bc.telemetry is None:
            continue
        if bc.telemetry.type == "json":
            adapters[bc.name] = JsonTelemetryAdapter(
                url=bc.telemetry.url, timeout_s=bc.telemetry.timeout_s
            )
            poll_interval = min(poll_interval, bc.telemetry.interval_s)
        else:
            logger.warning(
                "Backend %s: unknown telemetry type %r, skipping.",
                bc.name, bc.telemetry.type,
            )
    _telemetry_poller = TelemetryPoller(_registry, adapters, interval_s=poll_interval)
    if start_health:
        await _telemetry_poller.start()

    _request_logger = RequestLogger(_config.logging.jsonl_path)

    # Audit event publisher (fire-and-forget to Redpanda/Kafka).
    _audit_publisher = AuditEventPublisher(_config.audit_bus)
    await _audit_publisher.start()

    # Session affinity store (if enabled).
    if _config.session_affinity.enabled:
        from meridian.util.helpers import now_ms
        _session_store = SessionStore(
            ttl_ms=_config.session_affinity.ttl_s * 1000,
            max_sessions=_config.session_affinity.max_sessions,
            clock=now_ms,
        )
        logger.info(
            "Session affinity enabled — ttl=%ds, max=%d",
            _config.session_affinity.ttl_s,
            _config.session_affinity.max_sessions,
        )


async def shutdown_app() -> None:
    """Clean up app state."""
    if _health_checker:
        await _health_checker.stop()
    if _telemetry_poller:
        await _telemetry_poller.stop()
    if _audit_publisher:
        await _audit_publisher.stop()
    if _request_logger:
        _request_logger.close()
    await close_client()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_app()

    # Background sweep tasks (session affinity + rate-limit store idle TTL).
    sweep_tasks: list[asyncio.Task[None]] = []
    if _session_store is not None:
        async def _affinity_sweep() -> None:
            while True:
                await asyncio.sleep(_config.session_affinity.sweep_interval_s)  # type: ignore[union-attr]
                _session_store.sweep()  # type: ignore[union-attr]
        sweep_tasks.append(asyncio.create_task(_affinity_sweep()))

    if _config is not None and _config.rate_limit.enabled:
        async def _rl_sweep() -> None:
            while True:
                await asyncio.sleep(_config.rate_limit.sweep_interval_s)  # type: ignore[union-attr]
                _rate_limit.sweep()
        sweep_tasks.append(asyncio.create_task(_rl_sweep()))

    yield

    for task in sweep_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await shutdown_app()


app = FastAPI(title="Meridian", version="0.6.0", lifespan=lifespan)


def _error_json(message: str, error_type: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type}},
    )


@app.middleware("http")
async def _auth_gate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Enforce Bearer-key auth on /v1/* when enabled. All other paths open."""
    if _config is not None and _config.auth.enabled and request.url.path.startswith("/v1/"):
        try:
            request.state.identity = authenticate(request.headers.get("authorization"), _key_index)
        except AuthError as exc:
            return _error_json(exc.message, exc.error_type, 401)
    return await call_next(request)


def _build_request_context(body: Dict[str, Any]) -> RequestContext:
    """Estimate prompt tokens, resolve max_tokens, and precompute cost.

    Cost is `prompt * prefill_weight + max_tokens * decode_weight`. Computed
    once per request so each strategy doesn't re-derive it.
    """
    assert _config is not None
    prompt_tokens = estimate_prompt_tokens(body.get("messages"))
    max_tokens = extract_max_tokens(body, _config.gateway.default_max_tokens)
    cost = (
        prompt_tokens * _config.gateway.prefill_weight
        + max_tokens * _config.gateway.decode_weight
    )
    return RequestContext(prompt_tokens=prompt_tokens, max_tokens=max_tokens, cost=cost)


def _select_backend(
    model: str,
    tags: Optional[Set[str]] = None,
    request_ctx: Optional[RequestContext] = None,
) -> Optional[Backend]:
    assert _registry is not None and _strategy is not None
    eligible = _registry.eligible(model, tags)
    return _strategy.select(eligible, request_ctx)


def _select_with_tier(
    model: str,
    request_ctx: RequestContext,
) -> Tuple[Optional[Backend], Optional[str]]:
    """Select a backend, applying workload tiering when enabled.

    Returns (backend, tier_name). tier_name is None when tiering is disabled.
    When the matched tier's pool is empty, falls back to all healthy backends
    (reliability over isolation) and still returns the tier name for visibility.
    """
    assert _registry is not None and _strategy is not None and _config is not None
    if not _config.tiering.enabled:
        return _select_backend(model, request_ctx=request_ctx), None

    tier_name, tags = derive_tier(request_ctx, _config.tiering)
    eligible = _registry.eligible(model, tags)
    if not eligible:
        logger.warning(
            "Tier %r pool (tags=%s) has no healthy backend for model %r; "
            "falling back to all healthy backends.",
            tier_name, sorted(tags), model,
        )
        eligible = _registry.eligible(model, None)
    return _strategy.select(eligible, request_ctx), tier_name


def _route(
    model: str,
    request_ctx: RequestContext,
    session_id: Optional[str],
) -> Tuple[Optional[Backend], Optional[str], Optional[str]]:
    """Resolve (backend, tier_name, session_route).

    Affinity wins while healthy: if the session is pinned to a healthy backend
    that still serves ``model``, route to it (skipping tier+strategy) and return
    session_route="pinned". Otherwise route normally (tiering + strategy), pin
    the result, and return "new" (first time) or "remapped" (stale prior pin).
    session_route is None when affinity is disabled or no session id is given.
    """
    assert _config is not None and _registry is not None
    affinity_on = _config.session_affinity.enabled and session_id is not None

    session_route: Optional[str] = None
    if affinity_on and _session_store is not None:
        pinned_name = _session_store.get(session_id)  # type: ignore[arg-type]
        if pinned_name is not None:
            b = _registry.get(pinned_name)
            if b is not None and b.healthy and (not b.model or b.model == model):
                return b, None, "pinned"
            session_route = "remapped"  # had a pin but it was stale

    backend, tier_name = _select_with_tier(model, request_ctx)

    if backend is None:
        # No healthy backend was routed (the caller will 503). Don't report a
        # session outcome — a "remapped" with no backend is a contradictory
        # signal. The stale pin (if any) is left to expire / remap on recovery.
        return None, tier_name, None

    if affinity_on and _session_store is not None:
        _session_store.put(session_id, backend.name)  # type: ignore[arg-type]
        if session_route is None:
            session_route = "new"

    return backend, tier_name, session_route


def _finalize_request(
    *,
    request_id: str,
    model: str,
    stream: bool,
    backend: Backend,
    status_code: int,
    start: float,
    error_type: Optional[str],
    request_ctx: RequestContext,
    tier_name: Optional[str],
    session_route: Optional[str],
    org_id: Optional[str],
    team_id: Optional[str],
) -> None:
    """Sync request teardown: counters, logs, audit enqueue.

    Must not ``await`` — stream generators can be cancelled on client disconnect
    and any await in ``finally`` risks skipping the rest of cleanup (Milestone K).
    """
    assert _request_logger is not None
    latency = now_ms() - start
    backend.decrement_inflight()
    backend.subtract_inflight_cost(request_ctx.cost)
    backend.update_latency(latency)
    BACKEND_INFLIGHT.labels(backend=backend.name).dec()
    REQUESTS_TOTAL.labels(
        backend=backend.name,
        model=model,
        status=str(status_code),
        stream="true" if stream else "false",
    ).inc()
    REQUEST_LATENCY.labels(backend=backend.name, model=model).observe(latency)
    BACKEND_HEALTHY.labels(backend=backend.name).set(1 if backend.healthy else 0)
    _request_logger.log(
        request_id=request_id,
        model=model,
        stream=stream,
        backend=backend.name,
        status_code=status_code,
        latency_ms=latency,
        error_type=error_type,
        tier=tier_name,
        session_route=session_route,
        org_id=org_id,
        team_id=team_id,
    )
    _record_request(request_id, model, stream, backend.name, status_code, latency, error_type)
    if _audit_publisher is not None:
        _audit_publisher.enqueue(
            AuditEvent(
                request_id=request_id,
                model=model,
                stream=stream,
                backend=backend.name,
                status_code=status_code,
                latency_ms=latency,
                error_type=error_type,
                extra={"tier": tier_name, "org_id": org_id, "team_id": team_id},
            )
        )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    assert _registry is not None and _health_checker is not None
    assert _request_logger is not None and _config is not None

    # Identity is stashed on request.state by the auth middleware (only when
    # auth is enabled and the key validated). None otherwise. Metadata only —
    # never the key itself.
    identity: Optional[IdentityContext] = getattr(request.state, "identity", None)
    org_id = identity.org_id if identity else None
    team_id = identity.team_id if identity else None

    request_id = generate_request_id()
    start = now_ms()

    # Body size cap (Milestone K) — reject before buffering a multi-GB POST.
    max_body = _config.gateway.max_body_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_body:
                return _error_json(
                    f"Request body exceeds limit of {max_body} bytes",
                    "invalid_request_error",
                    413,
                )
        except ValueError:
            return _error_json("Invalid Content-Length header", "invalid_request_error", 400)

    raw_buf = bytearray()
    try:
        async for chunk in request.stream():
            raw_buf.extend(chunk)
            if len(raw_buf) > max_body:
                return _error_json(
                    f"Request body exceeds limit of {max_body} bytes",
                    "invalid_request_error",
                    413,
                )
        raw = bytes(raw_buf)
    except Exception:
        return _error_json("Failed to read request body", "invalid_request_error", 400)

    try:
        body: Dict[str, Any] = json.loads(raw) if raw else {}
    except Exception:
        return _error_json("Invalid JSON body", "invalid_request_error", 400)

    if not isinstance(body, dict):
        return _error_json("JSON body must be an object", "invalid_request_error", 400)

    model = body.get("model", "")
    is_stream = body.get("stream", False)

    # Order: access (403) → budgets (429) → rate limit (429) → route.
    # Access and budget denials must not spend a rate-limit token (Milestone J).
    if identity is not None and identity.scopes and model not in identity.scopes:
        return _error_json(
            f"Key is not permitted to use model {model!r}",
            "permission_error",
            403,
        )

    request_ctx = _build_request_context(body)

    # Tenant budgets (pre-flight estimate). Uses request_ctx.cost so we never
    # parse the upstream body; keeps streaming zero-copy.
    if (
        _usage_meter is not None
        and identity is not None
        and _config.budgets.enabled
    ):
        meter_keys = build_meter_keys(identity, _config.budgets)
        if meter_keys:
            decision = _usage_meter.check_and_increment(
                meter_keys, cost=request_ctx.cost, requests=1
            )
            if not decision.allowed:
                blocked = decision.blocked_key
                level = blocked.scope_level if blocked else "unknown"
                period = blocked.period if blocked else "unknown"
                BUDGET_REJECTIONS.labels(level=level, period=period).inc()
                logger.info(
                    "Budget exceeded request_id=%s level=%s period=%s org_id=%s team_id=%s",
                    request_id, level, period, org_id, team_id,
                )
                retry = decision.retry_after_s
                msg = (
                    f"Budget exceeded at {level} ({period})"
                    if blocked
                    else "Budget exceeded"
                )
                error_resp = _error_json(msg, "rate_limit_exceeded", 429)
                if retry is not None:
                    error_resp.headers["Retry-After"] = str(int(max(1, retry)))
                return error_resp

    # Using x-forwarded-for or x-real-ip headers first (best practise when behind a proxy)
    ip_address = request.headers.get("x-forwarded-for")
    if ip_address:
        ip_address = ip_address.split(",")[0].strip()
    else:
        ip_address = request.headers.get("x-real-ip")

    if not ip_address:
        ip_address = request.client.host if request.client else "127.0.0.1"

    # Rate-limit per tenant when authenticated (org-level), else per source IP.
    # Per-org overrides (budgets.orgs[*].token_capacity/refill) fold in here.
    rl_key = f"org:{org_id}" if org_id else f"ip:{ip_address}"
    if _config.rate_limit.enabled:
        cfg = _config.rate_limit
        capacity = cfg.token_capacity
        refill = cfg.token_refill_rate
        if org_id and _config.budgets.enabled:
            org_budget = _config.budgets.orgs.get(org_id)
            if org_budget is not None:
                if org_budget.token_capacity is not None:
                    capacity = org_budget.token_capacity
                if org_budget.token_refill_rate is not None:
                    refill = org_budget.token_refill_rate

        bucket = _rate_limit.get_or_create(rl_key, capacity, refill)

        if not bucket.allow_request():
            error_resp = _error_json(
                "Rate Limit Exceeded",
                "rate_limit_exceeded",
                429,
            )
            error_resp.headers["Retry-After"] = str(1 / bucket.refill_rate)
            return error_resp

    session_id = request.headers.get(_config.session_affinity.header) if _config.session_affinity.enabled else None
    backend, tier_name, session_route = _route(model, request_ctx, session_id)
    if backend is None:
        return _error_json(
            f"No healthy backend available for model {model!r}",
            "meridian_no_backend",
            503,
        )

    backend.increment_inflight()
    backend.add_inflight_cost(request_ctx.cost)
    BACKEND_INFLIGHT.labels(backend=backend.name).inc()

    status_code = 200
    error_type: Optional[str] = None

    try:
        if is_stream:
            resp = await forward_stream(backend, body, request)
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
                    _health_checker.check_passive_failure(backend)
                    error_type = type(exc).__name__
                    status_code = 502
                    raise
                except asyncio.CancelledError:
                    # Client disconnect mid-SSE — record then re-raise.
                    error_type = error_type or "client_disconnect"
                    status_code = 499
                    raise
                finally:
                    # All cleanup is synchronous (no await) so CancelledError
                    # cannot skip inflight/accounting/audit after a disconnect.
                    _finalize_request(
                        request_id=request_id,
                        model=model,
                        stream=True,
                        backend=backend,
                        status_code=status_code,
                        start=start,
                        error_type=error_type,
                        request_ctx=request_ctx,
                        tier_name=tier_name,
                        session_route=session_route,
                        org_id=org_id,
                        team_id=team_id,
                    )

            resp.body_iterator = tracked_stream()
            resp.headers["x-request-id"] = request_id
            resp.headers["x-meridian-backend"] = backend.name
            if tier_name is not None:
                resp.headers["x-meridian-tier"] = tier_name
            if session_route is not None:
                resp.headers["x-meridian-session-route"] = session_route
            return resp
        else:
            non_stream_resp = await forward_non_stream(backend, body, request)
            status_code = non_stream_resp.status_code
            if status_code >= 500:
                _health_checker.check_passive_failure(backend)
                error_type = "upstream_5xx"
            non_stream_resp.headers["x-request-id"] = request_id
            non_stream_resp.headers["x-meridian-backend"] = backend.name
            if tier_name is not None:
                non_stream_resp.headers["x-meridian-tier"] = tier_name
            if session_route is not None:
                non_stream_resp.headers["x-meridian-session-route"] = session_route
            return non_stream_resp

    except httpx.RequestError as exc:
        _health_checker.check_passive_failure(backend)
        error_type = type(exc).__name__
        status_code = 502
        return _error_json(
            f"Backend {backend.name!r} connection error: {exc}",
            "meridian_backend_error",
            502,
        )
    finally:
        if not is_stream:
            _finalize_request(
                request_id=request_id,
                model=model,
                stream=False,
                backend=backend,
                status_code=status_code,
                start=start,
                error_type=error_type,
                request_ctx=request_ctx,
                tier_name=tier_name,
                session_route=session_route,
                org_id=org_id,
                team_id=team_id,
            )


@app.get("/v1/models")
async def list_models() -> Response:
    assert _registry is not None
    backends = _registry.all_backends()
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
        "data": [{"id": m, "object": "model", "owned_by": "meridian"} for m in sorted(models)],
    })


@app.get("/meridian/status")
async def status() -> JSONResponse:
    assert _registry is not None and _config is not None
    return JSONResponse({
        "strategy": _config.gateway.strategy,
        "backends": [b.to_status_dict() for b in _registry.all_backends()],
    })


@app.get("/meridian/requests")
async def recent_requests() -> JSONResponse:
    return JSONResponse({"requests": list(_recent_requests)})


@app.get("/metrics")
async def metrics() -> Response:
    if _registry:
        for b in _registry.all_backends():
            BACKEND_HEALTHY.labels(backend=b.name).set(1 if b.healthy else 0)
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


_UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@app.get("/ui")
async def ui() -> FileResponse:
    return FileResponse(_UI_DIR / "index.html", media_type="text/html")
