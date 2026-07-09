"""Request policy pipeline — access, PII, budgets, rate limit.

Keeps ``chat_completions`` free of stacked special-case blocks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Request

from meridian.api.errors import GatewayError
from meridian.api.state import AppState
from meridian.auth import IdentityContext
from meridian.metrics.collectors import BUDGET_REJECTIONS, PII_DETECTIONS
from meridian.pii import apply_pii_policy, resolve_policy
from meridian.router.strategies import RequestContext
from meridian.router.token_estimator import estimate_prompt_tokens, extract_max_tokens
from meridian.usage import build_meter_keys

logger = logging.getLogger("meridian")


@dataclass
class ChatRequest:
    """Parsed, policy-checked chat request ready to route/proxy."""

    body: Dict[str, Any]
    model: str
    is_stream: bool
    identity: Optional[IdentityContext]
    org_id: Optional[str]
    team_id: Optional[str]
    request_ctx: RequestContext
    pii_counts: Optional[Dict[str, int]] = None
    request_id: str = ""
    start_ms: float = 0.0


async def read_json_body(request: Request, max_body: int) -> Dict[str, Any]:
    """Read and parse JSON with Content-Length + streaming size caps."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_body:
                raise GatewayError(
                    f"Request body exceeds limit of {max_body} bytes",
                    "invalid_request_error",
                    413,
                )
        except ValueError as exc:
            raise GatewayError(
                "Invalid Content-Length header", "invalid_request_error", 400
            ) from exc

    raw_buf = bytearray()
    try:
        async for chunk in request.stream():
            raw_buf.extend(chunk)
            if len(raw_buf) > max_body:
                raise GatewayError(
                    f"Request body exceeds limit of {max_body} bytes",
                    "invalid_request_error",
                    413,
                )
        raw = bytes(raw_buf)
    except GatewayError:
        raise
    except Exception as exc:
        raise GatewayError(
            "Failed to read request body", "invalid_request_error", 400
        ) from exc

    try:
        body: Any = json.loads(raw) if raw else {}
    except Exception as exc:
        raise GatewayError(
            "Invalid JSON body", "invalid_request_error", 400
        ) from exc

    if not isinstance(body, dict):
        raise GatewayError(
            "JSON body must be an object", "invalid_request_error", 400
        )
    return body


def build_request_context(state: AppState, body: Dict[str, Any]) -> RequestContext:
    gw = state.config.gateway
    prompt_tokens = estimate_prompt_tokens(body.get("messages"))
    max_tokens = extract_max_tokens(body, gw.default_max_tokens)
    cost = prompt_tokens * gw.prefill_weight + max_tokens * gw.decode_weight
    return RequestContext(
        prompt_tokens=prompt_tokens, max_tokens=max_tokens, cost=cost
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    return request.client.host if request.client else "127.0.0.1"


def apply_pii(
    state: AppState,
    body: Dict[str, Any],
    identity: Optional[IdentityContext],
    request_id: str,
    org_id: Optional[str],
) -> tuple[Dict[str, Any], Optional[Dict[str, int]]]:
    if not state.config.pii.enabled:
        return body, None

    key_override = identity.pii_policy if identity is not None else None
    policy = resolve_policy(state.config.pii.policy, key_override)
    entities = state.config.pii.entities or None
    decision = apply_pii_policy(body, policy=policy, entities=entities)
    counts: Optional[Dict[str, int]] = None
    if decision.counts:
        counts = decision.counts
        for ent, n in decision.counts.items():
            PII_DETECTIONS.labels(entity=ent, policy=policy).inc(n)
    if not decision.allowed:
        logger.info(
            "PII blocked request_id=%s policy=%s counts=%s org_id=%s",
            request_id, policy, decision.counts, org_id,
        )
        raise GatewayError(
            decision.message or "Request blocked: PII detected",
            "invalid_request_error",
            400,
        )
    out = decision.body if decision.body is not None else body
    return out, counts


def apply_budgets(
    state: AppState,
    identity: Optional[IdentityContext],
    request_ctx: RequestContext,
    request_id: str,
    org_id: Optional[str],
    team_id: Optional[str],
) -> None:
    """Pre-flight reserve. No automatic refund on upstream failure (by design)."""
    if state.usage_meter is None or identity is None or not state.config.budgets.enabled:
        return
    meter_keys = build_meter_keys(identity, state.config.budgets)
    if not meter_keys:
        return
    decision = state.usage_meter.check_and_increment(
        meter_keys, cost=request_ctx.cost, requests=1
    )
    if decision.allowed:
        return
    blocked = decision.blocked_key
    level = blocked.scope_level if blocked else "unknown"
    period = blocked.period if blocked else "unknown"
    BUDGET_REJECTIONS.labels(level=level, period=period).inc()
    logger.info(
        "Budget exceeded request_id=%s level=%s period=%s org_id=%s team_id=%s",
        request_id, level, period, org_id, team_id,
    )
    headers: Dict[str, str] = {}
    if decision.retry_after_s is not None:
        headers["Retry-After"] = str(int(max(1, decision.retry_after_s)))
    msg = (
        f"Budget exceeded at {level} ({period})" if blocked else "Budget exceeded"
    )
    raise GatewayError(msg, "rate_limit_exceeded", 429, headers=headers)


def apply_rate_limit(
    state: AppState,
    request: Request,
    org_id: Optional[str],
) -> None:
    if not state.config.rate_limit.enabled:
        return
    cfg = state.config.rate_limit
    capacity = cfg.token_capacity
    refill = cfg.token_refill_rate
    if org_id:
        override = cfg.org_overrides.get(org_id)
        if override is not None:
            if override.token_capacity is not None:
                capacity = override.token_capacity
            if override.token_refill_rate is not None:
                refill = override.token_refill_rate

    rl_key = f"org:{org_id}" if org_id else f"ip:{_client_ip(request)}"
    bucket = state.rate_limit.get_or_create(rl_key, capacity, refill)
    if not bucket.allow_request():
        raise GatewayError(
            "Rate Limit Exceeded",
            "rate_limit_exceeded",
            429,
            headers={"Retry-After": str(1 / bucket.refill_rate)},
        )


async def prepare_chat_request(
    state: AppState,
    request: Request,
    *,
    request_id: str,
    start_ms: float,
) -> ChatRequest:
    """Parse body and run policy chain. Raises GatewayError on deny."""
    identity: Optional[IdentityContext] = getattr(request.state, "identity", None)
    org_id = identity.org_id if identity else None
    team_id = identity.team_id if identity else None

    body = await read_json_body(request, state.config.gateway.max_body_bytes)
    model = body.get("model", "") or ""
    is_stream = bool(body.get("stream", False))

    # Order: access → PII → (cost) → budgets → rate limit
    if identity is not None and identity.allowed_models and model not in identity.allowed_models:
        raise GatewayError(
            f"Key is not permitted to use model {model!r}",
            "permission_error",
            403,
        )
    body, pii_counts = apply_pii(state, body, identity, request_id, org_id)
    request_ctx = build_request_context(state, body)
    apply_budgets(state, identity, request_ctx, request_id, org_id, team_id)
    apply_rate_limit(state, request, org_id)

    return ChatRequest(
        body=body,
        model=model,
        is_stream=is_stream,
        identity=identity,
        org_id=org_id,
        team_id=team_id,
        request_ctx=request_ctx,
        pii_counts=pii_counts,
        request_id=request_id,
        start_ms=start_ms,
    )
