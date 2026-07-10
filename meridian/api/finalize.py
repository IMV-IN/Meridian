"""Sync request teardown (stream-cancel safe)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from meridian.api.state import AppState
from meridian.audit.publisher import AuditEvent
from meridian.metrics.collectors import (
    BACKEND_HEALTHY,
    BACKEND_INFLIGHT,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
)
from meridian.registry.backend import Backend
from meridian.router.strategies import RequestContext
from meridian.util.helpers import now_ms


def finalize_request(
    state: AppState,
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
    pii_counts: Optional[Dict[str, int]] = None,
) -> None:
    """All teardown is synchronous — safe inside CancelledError finally blocks."""
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

    pii_meta = pii_counts if pii_counts else None
    state.request_logger.log(
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
        pii=pii_meta,
    )
    state.record_request(
        request_id, model, stream, backend.name, status_code, latency, error_type
    )
    extra: Dict[str, Any] = {
        "tier": tier_name,
        "org_id": org_id,
        "team_id": team_id,
    }
    if pii_meta is not None:
        extra["pii"] = pii_meta
    state.audit_publisher.enqueue(
        AuditEvent(
            request_id=request_id,
            model=model,
            stream=stream,
            backend=backend.name,
            status_code=status_code,
            latency_ms=latency,
            error_type=error_type,
            extra=extra,
        )
    )


def stamp_meridian_headers(
    headers: Any,
    *,
    request_id: str,
    backend: str,
    tier_name: Optional[str],
    session_route: Optional[str],
    budget_remaining_tokens: Optional[float] = None,
    budget_remaining_requests: Optional[float] = None,
) -> None:
    """Set x-request-id / x-meridian-* on a response headers mapping."""
    headers["x-request-id"] = request_id
    headers["x-meridian-backend"] = backend
    if tier_name is not None:
        headers["x-meridian-tier"] = tier_name
    if session_route is not None:
        headers["x-meridian-session-route"] = session_route
    # Post pre-flight reserve (estimate). Not re-stamped after reconcile.
    if budget_remaining_tokens is not None:
        headers["x-meridian-budget-remaining-tokens"] = _fmt_remaining(
            budget_remaining_tokens
        )
    if budget_remaining_requests is not None:
        headers["x-meridian-budget-remaining-requests"] = _fmt_remaining(
            budget_remaining_requests
        )


def _fmt_remaining(value: float) -> str:
    """Compact remaining value for headers (no scientific notation)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
