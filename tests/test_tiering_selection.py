"""Unit tests for the tier-aware selection helper in the API layer."""

import meridian.api.main as main
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.strategies import RequestContext, create_strategy


def _setup(monkeypatch, tier_tags, enabled=True):
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
    monkeypatch.setattr(main, "_registry", registry)
    monkeypatch.setattr(main, "_strategy", create_strategy("least_inflight"))
    monkeypatch.setattr(main, "_config", cfg)
    return registry


_TIERS = {
    "long_prompt": ["prefill-pool"],
    "long_decode": ["decode-pool"],
    "default": ["general"],
}


def _ctx(prompt, max_tok):
    return RequestContext(prompt_tokens=prompt, max_tokens=max_tok, cost=0.0)


def test_long_prompt_routes_to_prefill_pool(monkeypatch):
    _setup(monkeypatch, _TIERS)
    backend, tier = main._select_with_tier("m1", _ctx(5000, 100))
    assert tier == "long_prompt"
    assert backend.name == "prefill"


def test_default_routes_to_general_pool(monkeypatch):
    _setup(monkeypatch, _TIERS)
    backend, tier = main._select_with_tier("m1", _ctx(100, 100))
    assert tier == "default"
    assert backend.name == "general"


def test_empty_tier_falls_back_to_all_healthy(monkeypatch):
    # No backend tagged decode-pool; long_decode request must still be served.
    _setup(monkeypatch, _TIERS)
    backend, tier = main._select_with_tier("m1", _ctx(100, 2000))
    assert tier == "long_decode"
    # Fell back to the full healthy pool — one of the registered backends.
    assert backend is not None
    assert backend.name in {"prefill", "general"}


def test_tiering_disabled_returns_none_tier(monkeypatch):
    _setup(monkeypatch, _TIERS, enabled=False)
    backend, tier = main._select_with_tier("m1", _ctx(5000, 100))
    assert tier is None
    assert backend is not None
