"""Hot-reload: API keys (always) + full config (when a config file is known).

SIGHUP / POST /meridian/reload. Atomic swap: the new config is parsed and all
derived components are built before any reference on ``state`` changes — a bad
file leaves the running state untouched. In-flight requests keep the objects
they already resolved; new requests see the new ones.

Full reload covers: strategy + weights, backends, tiering rules, auth keys,
health thresholds, resilience knobs, session-affinity config, rate-limit store
bounds, telemetry adapters. Budget/cost stores, audit bus, and the JSONL log
path keep their running objects (data continuity) — changing those sections is
logged as requiring a restart.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict

from meridian.auth.keys import rebuild_key_index
from meridian.config.models import MeridianConfig
from meridian.metrics.collectors import BACKEND_HEALTHY, BACKEND_INFLIGHT
from meridian.router.affinity import SessionStore
from meridian.router.strategies import create_strategy
from meridian.telemetry import JsonTelemetryAdapter, TelemetryAdapter, TelemetryPoller
from meridian.usage import InMemoryUsageMeter, SqliteUsageMeter
from meridian.util.helpers import now_ms

if TYPE_CHECKING:
    from meridian.api.ratelimitter import RateLimitStore  # noqa: F401
    from meridian.api.state import AppState

logger = logging.getLogger("meridian")

_reload_lock = threading.Lock()


def reload_keys(state: "AppState") -> int:
    """Rebuild key index from config + keys_file. Returns number of keys loaded.

    Raises ValueError/OSError on bad file or duplicate keys (state unchanged).
    """
    with _reload_lock:
        new_index = rebuild_key_index(state.config.auth)
        state.key_index = new_index
        n = len(new_index)
        logger.info("Reloaded API keys — %d key(s) active", n)
        return n


def _warn_if_changed(old: Any, new: Any, section: str, why: str) -> None:
    if old != new:
        logger.warning(
            "Config section %r changed but requires restart (%s) — keeping running state",
            section, why,
        )


async def reload_config(state: "AppState") -> Dict[str, Any]:
    """Re-read the config file and swap derived components atomically.

    Falls back to keys-only reload when the process was started from an
    in-memory config (tests; ``state.config_path is None``).
    """
    if state.config_path is None:
        n = reload_keys(state)
        return {"scope": "keys", "keys": n}

    # Parse + validate before touching anything — raises → state unchanged.
    new_cfg = MeridianConfig.from_yaml(state.config_path)

    with _reload_lock:
        # Build everything that might fail BEFORE mutating state.
        new_index = rebuild_key_index(new_cfg.auth)
        from meridian.api.state import build_registry  # local: avoid import cycle

        new_registry = build_registry(new_cfg)
        new_strategy = create_strategy(
            new_cfg.gateway.strategy,
            prefill_weight=new_cfg.gateway.prefill_weight,
            decode_weight=new_cfg.gateway.decode_weight,
            default_max_tokens=new_cfg.gateway.default_max_tokens,
            queue_weight=new_cfg.gateway.queue_weight,
            mem_weight=new_cfg.gateway.mem_weight,
        )

        old_cfg = state.config

        # Swap the stateless runtime objects.
        state.config = new_cfg
        state.registry = new_registry
        state.strategy = new_strategy
        state.key_index = new_index
        state.health_checker.registry = new_registry
        state.health_checker.update_config(new_cfg.health)

        for b in new_registry.all_backends():
            BACKEND_HEALTHY.labels(backend=b.name).set(1 if b.healthy else 0)
            BACKEND_INFLIGHT.labels(backend=b.name).set(0)

        # Budgets: off→on spins up a fresh meter; any other change keeps the
        # running meter (data continuity) and asks for a restart.
        if new_cfg.budgets != old_cfg.budgets:
            if not old_cfg.budgets.enabled and new_cfg.budgets.enabled:
                state.usage_meter = (
                    InMemoryUsageMeter()
                    if new_cfg.budgets.store == "memory"
                    else SqliteUsageMeter(new_cfg.budgets.sqlite_path)
                )
            else:
                logger.warning(
                    "Config section 'budgets' changed; usage meter kept — "
                    "restart required to switch/drop stores"
                )

        # Sections that keep running objects (data continuity / open handles).
        _warn_if_changed(old_cfg.cost, new_cfg.cost, "cost", "ledger data continuity")
        _warn_if_changed(old_cfg.audit_bus, new_cfg.audit_bus, "audit_bus", "publisher lifecycle")
        _warn_if_changed(old_cfg.logging, new_cfg.logging, "logging", "open JSONL handle")

        # Rebuild bounded stores whose configs changed (old buckets/pins dropped).
        if new_cfg.rate_limit != old_cfg.rate_limit:
            from meridian.api.ratelimitter import RateLimitStore

            rl: "RateLimitStore" = RateLimitStore(
                max_keys=new_cfg.rate_limit.max_buckets,
                idle_ttl_s=new_cfg.rate_limit.idle_ttl_s,
            )
            state.rate_limit = rl
            logger.warning(
                "Rate-limit store rebuilt on config reload — all per-key tokens reset"
            )

        if new_cfg.session_affinity != old_cfg.session_affinity:
            if old_cfg.session_affinity.enabled != new_cfg.session_affinity.enabled or (
                new_cfg.session_affinity.enabled
                and (
                    new_cfg.session_affinity.ttl_s != old_cfg.session_affinity.ttl_s
                    or new_cfg.session_affinity.max_sessions
                    != old_cfg.session_affinity.max_sessions
                )
            ):
                logger.warning(
                    "Session affinity config changed — existing pinned sessions dropped"
                )
            sa = new_cfg.session_affinity
            state.session_store = (
                SessionStore(ttl_ms=sa.ttl_s * 1000, max_sessions=sa.max_sessions, clock=now_ms)
                if sa.enabled
                else None
            )

        logger.info(
            "Config reloaded from %s — strategy=%s, %d backend(s), %d key(s)",
            state.config_path,
            new_cfg.gateway.strategy,
            len(new_registry.all_backends()),
            len(new_index),
        )

    # Telemetry poller restart is async — outside the lock.
    # Roll back to the old poller if the new one fails to start.
    old_poller = state.telemetry_poller
    try:
        await _sync_telemetry_poller(state)
    except Exception:
        state.telemetry_poller = old_poller
        logger.exception("Telemetry poller rebuild failed — keeping previous poller")
        raise
    return {"scope": "full", "keys": len(state.key_index)}


async def _sync_telemetry_poller(state: "AppState") -> None:
    """Rebuild the telemetry poller for the current config; restart if it ran."""
    was_running = state.telemetry_poller._task is not None
    if was_running:
        await state.telemetry_poller.stop()

    adapters: Dict[str, TelemetryAdapter] = {}
    interval = 5.0
    for bc in state.config.backends:
        if bc.telemetry is None:
            continue
        if bc.telemetry.type == "json":
            adapters[bc.name] = JsonTelemetryAdapter(
                url=bc.telemetry.url, timeout_s=bc.telemetry.timeout_s
            )
            interval = min(interval, bc.telemetry.interval_s)
        else:
            logger.warning(
                "Backend %s: unknown telemetry type %r, skipping.",
                bc.name, bc.telemetry.type,
            )

    poller = TelemetryPoller(state.registry, adapters, interval_s=interval)
    state.telemetry_poller = poller
    if was_running:
        await poller.start()
