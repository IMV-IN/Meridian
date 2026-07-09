"""Backend selection: tiering + session affinity + strategy."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from meridian.api.state import AppState
from meridian.registry.backend import Backend
from meridian.router.strategies import RequestContext
from meridian.router.tiering import derive_tier

logger = logging.getLogger("meridian")


def select_with_tier(
    state: AppState,
    model: str,
    request_ctx: RequestContext,
) -> Tuple[Optional[Backend], Optional[str]]:
    if not state.config.tiering.enabled:
        eligible = state.registry.eligible(model, None)
        return state.strategy.select(eligible, request_ctx), None

    tier_name, tags = derive_tier(request_ctx, state.config.tiering)
    eligible = state.registry.eligible(model, tags)
    if not eligible:
        logger.warning(
            "Tier %r pool (tags=%s) has no healthy backend for model %r; "
            "falling back to all healthy backends.",
            tier_name, sorted(tags), model,
        )
        eligible = state.registry.eligible(model, None)
    return state.strategy.select(eligible, request_ctx), tier_name


def route(
    state: AppState,
    model: str,
    request_ctx: RequestContext,
    session_id: Optional[str],
) -> Tuple[Optional[Backend], Optional[str], Optional[str]]:
    """Resolve (backend, tier_name, session_route)."""
    cfg = state.config
    affinity_on = cfg.session_affinity.enabled and session_id is not None
    store = state.session_store

    session_route: Optional[str] = None
    if affinity_on and store is not None:
        pinned_name = store.get(session_id)  # type: ignore[arg-type]
        if pinned_name is not None:
            b = state.registry.get(pinned_name)
            if b is not None and b.healthy and (not b.model or b.model == model):
                return b, None, "pinned"
            session_route = "remapped"

    backend, tier_name = select_with_tier(state, model, request_ctx)
    if backend is None:
        return None, tier_name, None

    if affinity_on and store is not None:
        store.put(session_id, backend.name)  # type: ignore[arg-type]
        if session_route is None:
            session_route = "new"

    return backend, tier_name, session_route
