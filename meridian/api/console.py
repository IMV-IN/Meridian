"""Read models for the operator console (``/ui``).

Three views, all metadata-only:

- ``whoami_view``    — the presenting key's own identity + derived rights, so
  the console can show/hide actions instead of discovering 403s by trial.
- ``config_summary`` — the effective routing / policy configuration.
- ``budget_snapshot`` — current consumption against every configured cap.

Redaction is by **allowlist**: each view copies named fields only. Raw API
keys, upstream ``auth_header`` values, telemetry/log/db file paths and the
keys_file path never appear, so adding a secret field to the config schema
cannot leak it here by accident.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from meridian.auth import IdentityContext
from meridian.config.models import ScopeBudget
from meridian.pii.types import ALL_ENTITIES
from meridian.usage.bucket import period_bucket, retry_after_s
from meridian.usage.types import MeterKey

if TYPE_CHECKING:
    from meridian.api.state import AppState


def _features(state: "AppState") -> Dict[str, bool]:
    cfg = state.config
    return {
        "cost": state.cost_ledger is not None,
        "budgets": state.usage_meter is not None,
        "key_lifecycle": cfg.auth.enabled and bool(cfg.auth.keys_file),
        "control_plane": cfg.control_plane.enabled,
        "canary": state.canary is not None,
        "pii": cfg.pii.enabled,
    }


def _cloud(state: "AppState") -> Optional[Dict[str, Any]]:
    """Meridian Cloud connection, when this gateway reports to a workspace.
    The fleet (nodes, enrollment, serving) is then managed in Meridian Cloud."""
    cp = state.config.control_plane
    if not (cp.enabled and cp.url and cp.resolved_token()):
        return None
    return {"url": cp.url.rstrip("/"), "sign_in": state.cloud_sso is not None}


def whoami_view(state: "AppState", identity: Optional[IdentityContext]) -> Dict[str, Any]:
    """Identity + capabilities for the presenting key (or open mode)."""
    auth_enabled = state.config.auth.enabled
    if identity is None:
        # Open mode: ops views are readable without a key; anything that
        # requires auth (reload, key lifecycle, cost export) is unavailable.
        return {
            "auth_enabled": auth_enabled,
            "identity": None,
            "capabilities": {
                "view_ops": True,
                "reload": False,
                "manage_keys": False,
                "read_all_cost": False,
            },
            "features": _features(state),
            "cloud": _cloud(state),
        }
    return {
        "auth_enabled": auth_enabled,
        "identity": {
            "key_id": identity.key_id,
            "org_id": identity.org_id,
            "team_id": identity.team_id,
            "user_id": identity.user_id,
            "role": identity.role,
            "cost_admin": identity.cost_admin,
            "ops_admin": identity.ops_admin,
            "allowed_models": sorted(identity.allowed_models),
            "pii_policy": identity.pii_policy,
        },
        "capabilities": {
            "view_ops": identity.can_view_ops,
            "reload": identity.can_reload,
            "manage_keys": identity.can_manage_keys,
            "read_all_cost": identity.can_read_all_cost,
        },
        "features": _features(state),
        "cloud": _cloud(state),
    }


def _bucket_caps(limit: Any) -> Optional[Dict[str, float]]:
    if limit is None:
        return None
    return {
        "token_capacity": limit.token_capacity,
        "token_refill_rate": limit.token_refill_rate,
    }


def config_summary(state: "AppState") -> Dict[str, Any]:
    """Effective configuration for the console (allowlisted, no secrets)."""
    from meridian import __version__

    cfg = state.config
    gw = cfg.gateway
    ca = gw.cache_affinity
    rl = cfg.rate_limit
    cb = cfg.resilience.circuit_breaker
    canary = cfg.canary
    active_sessions = (
        state.session_store.size() if state.session_store is not None else None
    )
    return {
        "version": __version__,
        "reloadable": state.config_path is not None,
        "features": _features(state),
        "gateway": {
            "strategy": gw.strategy,
            "prefill_weight": gw.prefill_weight,
            "decode_weight": gw.decode_weight,
            "default_max_tokens": gw.default_max_tokens,
            "token_estimator": gw.token_estimator,
            "queue_weight": gw.queue_weight,
            "mem_weight": gw.mem_weight,
            "max_body_bytes": gw.max_body_bytes,
            "cache_affinity": {
                "block_chars": ca.block_chars,
                "max_checkpoints": ca.max_checkpoints,
                "ttl_s": ca.ttl_s,
                "max_entries": ca.max_entries,
                "locality_weight": ca.locality_weight,
                "hit_threshold": ca.hit_threshold,
                "min_trust": ca.min_trust,
                "feedback_correction": ca.feedback_correction,
            },
        },
        "health": cfg.health.model_dump(),
        "timeouts": cfg.timeouts.model_dump(),
        "resilience": {
            "max_retries": cfg.resilience.max_retries,
            "retry_backoff_base": cfg.resilience.retry_backoff_base,
            "circuit_breaker": {
                "enabled": cb.enabled,
                "failure_threshold": cb.failure_threshold,
                "open_seconds": cb.open_seconds,
            },
        },
        "tiering": {
            "enabled": cfg.tiering.enabled,
            "long_prompt_tokens": cfg.tiering.long_prompt_tokens,
            "long_decode_tokens": cfg.tiering.long_decode_tokens,
            "tiers": {k: list(v) for k, v in cfg.tiering.tiers.items()},
        },
        "session_affinity": {
            "enabled": cfg.session_affinity.enabled,
            "header": cfg.session_affinity.header,
            "ttl_s": cfg.session_affinity.ttl_s,
            "max_sessions": cfg.session_affinity.max_sessions,
            "active_sessions": active_sessions,
        },
        "canary": {
            "enabled": canary.enabled,
            "canary_tags": list(canary.canary_tags),
            "stable_tags": list(canary.stable_tags),
            "start_weight": canary.start_weight,
            "steps": [
                {"weight": s.weight, "duration_s": s.duration_s} for s in canary.steps
            ],
            "rollback_error_rate": canary.rollback_error_rate,
            "rollback_min_samples": canary.rollback_min_samples,
            "window_s": canary.window_s,
            "tick_s": canary.tick_s,
        },
        "isolation": {
            "mode": cfg.isolation.mode,
            "pools": {org: list(tags) for org, tags in cfg.isolation.pools.items()},
        },
        "rate_limit": {
            "enabled": rl.enabled,
            "token_capacity": rl.token_capacity,
            "token_refill_rate": rl.token_refill_rate,
            "max_buckets": rl.max_buckets,
            "idle_ttl_s": rl.idle_ttl_s,
            "global": _bucket_caps(rl.global_limit),
            "models": {m: _bucket_caps(o) for m, o in rl.models.items()},
            "org_overrides": {o: _bucket_caps(v) for o, v in rl.org_overrides.items()},
        },
        "budgets": {
            "enabled": cfg.budgets.enabled,
            "store": cfg.budgets.store,
            "scopes": {
                "orgs": len(cfg.budgets.orgs),
                "teams": len(cfg.budgets.teams),
                "users": len(cfg.budgets.users),
                "keys": len(cfg.budgets.keys),
            },
        },
        "pii": {
            "enabled": cfg.pii.enabled,
            "policy": cfg.pii.policy,
            "entities": list(cfg.pii.entities) or list(ALL_ENTITIES),
            "supported_entities": list(ALL_ENTITIES),
        },
        "cost": {
            "enabled": cfg.cost.enabled,
            "store": cfg.cost.store,
            "currency": cfg.cost.currency,
            "max_window_days": cfg.cost.max_window_days,
            "default_prompt_per_1m": cfg.cost.default_prompt_per_1m,
            "default_completion_per_1m": cfg.cost.default_completion_per_1m,
            "models": {
                m: {
                    "prompt_per_1m": p.prompt_per_1m,
                    "completion_per_1m": p.completion_per_1m,
                }
                for m, p in cfg.cost.models.items()
            },
        },
        "auth": {
            "enabled": cfg.auth.enabled,
            "key_count": len(state.key_index),
            "keys_file_configured": bool(cfg.auth.keys_file),
        },
        "audit_bus": {
            "enabled": cfg.audit_bus.enabled,
            "topic": cfg.audit_bus.topic,
        },
        "control_plane": {
            "enabled": cfg.control_plane.enabled,
            "url": cfg.control_plane.url,
            "poll_interval_s": cfg.control_plane.poll_interval_s,
            "tag": cfg.control_plane.tag,
            # Whether a Meridian Cloud token is present — never the token itself.
            "cloud_connected": bool(cfg.control_plane.resolved_token()),
            "report": cfg.control_plane.report,
        },
        "backends": [
            {
                "name": bc.name,
                "health_endpoint": bc.health_endpoint,
                "telemetry": (
                    {"type": bc.telemetry.type, "interval_s": bc.telemetry.interval_s}
                    if bc.telemetry is not None
                    else None
                ),
                "idle_timeout_min": bc.idle_timeout_min,
                "timeout_override": (
                    bc.timeout.model_dump(exclude_none=True) if bc.timeout else None
                ),
                "upstream_auth": bc.auth_header is not None,
            }
            for bc in cfg.backends
        ],
    }


def _scope_rows(
    state: "AppState",
    scope: ScopeBudget,
    level: str,
    scope_id: str,
    now: datetime,
) -> List[Dict[str, Any]]:
    assert state.usage_meter is not None
    rows: List[Dict[str, Any]] = []
    for period in ("daily", "monthly"):
        caps = getattr(scope, period)
        if caps is None:
            continue
        bucket = period_bucket(period, now)
        resets_at = now + timedelta(seconds=retry_after_s(period, now))
        for metric in ("tokens", "requests"):
            cap = getattr(caps, metric)
            if cap is None:
                continue
            key = MeterKey(
                scope_level=level,
                scope_id=scope_id,
                period=period,
                period_bucket=bucket,
                metric=metric,
                cap=cap,
            )
            consumed = state.usage_meter.usage(key).consumed
            rows.append({
                "scope_level": level,
                "scope_id": scope_id,
                "period": period,
                "bucket": bucket,
                "metric": metric,
                "cap": cap,
                "consumed": round(consumed, 4),
                "remaining": round(max(0.0, cap - consumed), 4),
                "utilization": round(consumed / cap, 6) if cap else None,
                "resets_at": resets_at.isoformat().replace("+00:00", "Z"),
            })
    return rows


def budget_snapshot(
    state: "AppState", now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Current-period consumption for every configured budget cap."""
    if state.usage_meter is None:
        return {"enabled": False, "rows": []}
    now = now or datetime.now(timezone.utc)
    b = state.config.budgets
    rows: List[Dict[str, Any]] = []
    for level, scopes in (
        ("org", b.orgs),
        ("team", b.teams),
        ("user", b.users),
        ("key", b.keys),
    ):
        for scope_id, scope in sorted(scopes.items()):
            rows.extend(_scope_rows(state, scope, level, scope_id, now))
    return {
        "enabled": True,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "rows": rows,
    }
