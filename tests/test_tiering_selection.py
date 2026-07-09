"""Unit tests for tier-aware selection (meridian.api.routing)."""

from __future__ import annotations

from types import SimpleNamespace

from meridian.api.routing import select_with_tier
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.strategies import RequestContext, create_strategy


def _state(tier_tags, enabled=True):
    backends = [
        Backend(BackendConfig(name="prefill", url="http://p", model="m1", tags=["prefill-pool"])),
        Backend(BackendConfig(name="general", url="http://g", model="m1", tags=["general"])),
    ]
    registry = BackendRegistry(backends)
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "tiering": {
            "enabled": enabled,
            "long_prompt_tokens": 4000,
            "long_decode_tokens": 1000,
            "tiers": tier_tags,
        },
    })
    return SimpleNamespace(
        registry=registry,
        strategy=create_strategy("least_inflight"),
        config=cfg,
    )


_TIERS = {
    "long_prompt": ["prefill-pool"],
    "long_decode": ["decode-pool"],
    "default": ["general"],
}


def _ctx(prompt, max_tok):
    return RequestContext(prompt_tokens=prompt, max_tokens=max_tok, cost=0.0)


def test_long_prompt_routes_to_prefill_pool():
    st = _state(_TIERS)
    backend, tier = select_with_tier(st, "m1", _ctx(5000, 100))  # type: ignore[arg-type]
    assert tier == "long_prompt"
    assert backend.name == "prefill"


def test_default_routes_to_general_pool():
    st = _state(_TIERS)
    backend, tier = select_with_tier(st, "m1", _ctx(100, 100))  # type: ignore[arg-type]
    assert tier == "default"
    assert backend.name == "general"


def test_empty_tier_falls_back_to_all_healthy():
    st = _state(_TIERS)
    backend, tier = select_with_tier(st, "m1", _ctx(100, 2000))  # type: ignore[arg-type]
    assert tier == "long_decode"
    assert backend is not None
    assert backend.name in {"prefill", "general"}


def test_tiering_disabled_returns_none_tier():
    st = _state(_TIERS, enabled=False)
    backend, tier = select_with_tier(st, "m1", _ctx(5000, 100))  # type: ignore[arg-type]
    assert tier is None
    assert backend is not None
