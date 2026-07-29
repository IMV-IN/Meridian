"""Backend selection: isolation + tiering + session affinity + strategy."""

from __future__ import annotations

import logging
from typing import List, Optional, Set, Tuple

from meridian.api.state import AppState
from meridian.registry.backend import Backend
from meridian.router.isolation import filter_visible, org_can_use, org_pool_tags
from meridian.router.strategies import RequestContext
from meridian.router.tiering import derive_tier
from meridian.util.helpers import now_ms

logger = logging.getLogger("meridian")


def _wake(state: AppState, idle: List[Backend], request_ctx: RequestContext, model: str) -> Optional[Backend]:
    chosen = state.strategy.select(idle, request_ctx)
    if chosen is not None:
        chosen.wake(now_ms())
        logger.info(
            "Woke idle backend %s for model %r (scale-to-zero)",
            chosen.name, model,
        )
    return chosen


def _dedicated_select(
    state: AppState,
    model: str,
    request_ctx: RequestContext,
    pool_tags: Set[str],
    tier_tags: Optional[Set[str]],
) -> Optional[Backend]:
    """Listed org in dedicated mode: its pool, tier sub-pool preferred.

    Fallback order: tier∩pool → pool (active) → tier∩pool → pool (idle wake).
    Never touches backends outside the org's pool — that containment is the
    guarantee dedicated mode sells.
    """
    if tier_tags is not None:
        combined = pool_tags | tier_tags
        chosen = state.strategy.select(
            state.registry.eligible(model, combined), request_ctx
        )
        if chosen is not None:
            return chosen
    chosen = state.strategy.select(
        state.registry.eligible(model, pool_tags), request_ctx
    )
    if chosen is not None:
        return chosen
    if tier_tags is not None:
        combined = pool_tags | tier_tags
        chosen = _wake(
            state, state.registry.idle_eligible(model, combined), request_ctx, model
        )
        if chosen is not None:
            return chosen
    return _wake(
        state, state.registry.idle_eligible(model, pool_tags), request_ctx, model
    )


def select_with_tier(
    state: AppState,
    model: str,
    request_ctx: RequestContext,
    org_id: Optional[str] = None,
) -> Tuple[Optional[Backend], Optional[str]]:
    """Select among backends the org may see; on miss, wake a matching idle one.

    Shared mode order: active tier pool → active full pool → idle tier pool →
    idle full pool. Dedicated mode: pinned orgs are confined to their pool
    (see :func:`_dedicated_select`); unlisted orgs never touch reserved pools.
    Waking marks the backend active so the request routes to it (and an
    external scaler can spin its replicas back up); health state is untouched.
    """
    tier_name: Optional[str] = None
    tags = None
    if state.config.tiering.enabled:
        tier_name, tags = derive_tier(request_ctx, state.config.tiering)

    iso = state.config.isolation
    if iso.mode == "dedicated":
        pool_tags = org_pool_tags(iso, org_id)
        if pool_tags is not None:
            chosen = _dedicated_select(state, model, request_ctx, pool_tags, tags)
            return chosen, tier_name
        # Unlisted org: all healthy backends minus every reserved pool.
        eligible = filter_visible(iso, org_id, state.registry.eligible(model, tags))
        if not eligible and tags is not None:
            eligible = filter_visible(iso, org_id, state.registry.eligible(model, None))
        chosen = state.strategy.select(eligible, request_ctx)
        if chosen is None:
            idle = filter_visible(iso, org_id, state.registry.idle_eligible(model, tags))
            if not idle and tags is not None:
                idle = filter_visible(iso, org_id, state.registry.idle_eligible(model, None))
            chosen = _wake(state, idle, request_ctx, model)
        return chosen, tier_name

    eligible = state.registry.eligible(model, tags)
    if not eligible and tags is not None:
        logger.warning(
            "Tier %r pool (tags=%s) has no healthy backend for model %r; "
            "falling back to all healthy backends.",
            tier_name, sorted(tags), model,
        )
        eligible = state.registry.eligible(model, None)

    chosen = state.strategy.select(eligible, request_ctx)
    if chosen is None:
        idle = state.registry.idle_eligible(model, tags)
        if not idle and tags is not None:
            idle = state.registry.idle_eligible(model, None)
        chosen = _wake(state, idle, request_ctx, model)
    return chosen, tier_name


def route(
    state: AppState,
    model: str,
    request_ctx: RequestContext,
    session_id: Optional[str],
    org_id: Optional[str] = None,
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
            if (
                b is not None
                and b.healthy
                and not b.idle
                and (not b.model or b.model == model)
                and org_can_use(cfg.isolation, org_id, b)
            ):
                return b, None, "pinned"
            # Pinned backend unreachable OR no longer in the org's pool
            # (isolation remap) — fall through and pin a fresh one.
            session_route = "remapped"

    backend, tier_name = select_with_tier(state, model, request_ctx, org_id)
    if backend is None:
        return None, tier_name, None

    if affinity_on and store is not None:
        store.put(session_id, backend.name)  # type: ignore[arg-type]
        if session_route is None:
            session_route = "new"

    return backend, tier_name, session_route
