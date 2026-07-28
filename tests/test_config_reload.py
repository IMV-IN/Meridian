"""Phase 1: full dynamic config reload (POST /meridian/reload + SIGHUP path).

Covered:
- keys-only fallback when no config file is known (in-memory config / tests)
- full reload swaps strategy, backends, and health knobs atomically
- invalid new config is rejected and running state is untouched
- the HTTP endpoint exposes scope=full when a config file backs the process
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.api.reload import reload_config
from meridian.config.models import MeridianConfig

KEY_OPS = "mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh"


def _write_config(path: Path, **overrides) -> None:
    data = {
        "gateway": {"strategy": "least_inflight"},
        "health": {"interval_s": 5.0, "fail_threshold": 2},
        "logging": {"jsonl_path": str(path.parent / "requests.jsonl")},
        "auth": {"enabled": True, "keys": [{"key": KEY_OPS, "org_id": "ops", "ops_admin": True}]},
        "backends": [{"name": "b1", "url": "http://127.0.0.1:9001", "model": "m"}],
    }
    for k, v in overrides.items():
        data[k] = v
    path.write_text(yaml.dump(data))


@pytest.mark.asyncio
async def test_keys_only_fallback_without_config_file():
    """init_app(config=...) → no file: reload keeps current keys-only scope."""
    cfg = MeridianConfig.from_dict({
        "auth": {"enabled": True, "keys": [{"key": KEY_OPS, "org_id": "ops"}]},
        "backends": [],
    })
    await init_app(cfg, start_health=False)
    report = await reload_config(get_state())
    assert report == {"scope": "keys", "keys": 1}


@pytest.mark.asyncio
async def test_full_reload_swaps_strategy_and_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = tmp_path / "config.yaml"
    _write_config(cfg_file)
    monkeypatch.setenv("MERIDIAN_CONFIG", str(cfg_file))
    monkeypatch.chdir(tmp_path)

    await init_app(start_health=False)
    state = get_state()
    assert state.config.gateway.strategy == "least_inflight"
    assert [b.name for b in state.registry.all_backends()] == ["b1"]
    assert state.health_checker.config.fail_threshold == 2
    old_registry = state.registry

    _write_config(
        cfg_file,
        gateway={"strategy": "weighted_round_robin"},
        health={"interval_s": 1.0, "fail_threshold": 5},
        backends=[
            {"name": "b1", "url": "http://127.0.0.1:9001", "model": "m", "weight": 3},
            {"name": "b2", "url": "http://127.0.0.1:9002", "model": "m", "weight": 2},
        ],
    )
    report = await reload_config(state)

    assert report["scope"] == "full"
    assert report["keys"] == 1
    # Strategy + weights
    assert state.config.gateway.strategy == "weighted_round_robin"
    assert type(state.strategy).__name__ == "WeightedRoundRobin"  # object rebuilt
    # Backends swapped atomically — registry is a NEW object
    assert state.registry is not old_registry
    assert [b.name for b in state.registry.all_backends()] == ["b1", "b2"]
    assert state.registry.get("b1").weight == 3  # type: ignore[union-attr]
    # Health knobs picked up without restart
    assert state.health_checker.config.fail_threshold == 5
    # Health checker points at the new registry
    assert state.health_checker.registry is state.registry


@pytest.mark.asyncio
async def test_bad_config_rejected_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = tmp_path / "config.yaml"
    _write_config(cfg_file)
    monkeypatch.setenv("MERIDIAN_CONFIG", str(cfg_file))
    monkeypatch.chdir(tmp_path)

    await init_app(start_health=False)
    state = get_state()
    orig_config, orig_registry, orig_strategy, orig_keys = (
        state.config, state.registry, state.strategy, state.key_index,
    )

    # Unknown strategy + malformed backend → validation error, keep old state
    _write_config(cfg_file, gateway={"strategy": "does_not_exist"})
    with pytest.raises(Exception):
        await reload_config(state)

    assert state.config is orig_config
    assert state.registry is orig_registry
    assert state.strategy is orig_strategy
    assert state.key_index is orig_keys


@pytest.mark.asyncio
async def test_invalid_yaml_rejected_state_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = tmp_path / "config.yaml"
    _write_config(cfg_file)
    monkeypatch.setenv("MERIDIAN_CONFIG", str(cfg_file))
    monkeypatch.chdir(tmp_path)

    await init_app(start_health=False)
    state = get_state()
    orig_registry = state.registry

    cfg_file.write_text("gateway: [this is not valid yaml: {}")
    with pytest.raises(Exception):
        await reload_config(state)
    assert state.registry is orig_registry


@pytest.mark.asyncio
async def test_reload_endpoint_full_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = tmp_path / "config.yaml"
    _write_config(cfg_file)
    monkeypatch.setenv("MERIDIAN_CONFIG", str(cfg_file))
    monkeypatch.chdir(tmp_path)

    await init_app(start_health=False)

    _write_config(cfg_file, gateway={"strategy": "ewma_latency"})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/meridian/reload", headers={"Authorization": f"Bearer {KEY_OPS}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reloaded"] is True
        assert body["scope"] == "full"
        assert body["keys"] == 1
    assert get_state().config.gateway.strategy == "ewma_latency"


@pytest.mark.asyncio
async def test_tiering_rules_take_effect_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg_file = tmp_path / "config.yaml"
    _write_config(cfg_file)
    monkeypatch.setenv("MERIDIAN_CONFIG", str(cfg_file))
    monkeypatch.chdir(tmp_path)

    await init_app(start_health=False)
    state = get_state()
    assert state.config.tiering.enabled is False

    _write_config(cfg_file, tiering={
        "enabled": True,
        "long_prompt_tokens": 100,
        "long_decode_tokens": 50,
        "tiers": {
            "long_prompt": ["prefill"],
            "long_decode": ["decode"],
            "default": ["general"],
        },
    })
    await reload_config(state)
    assert state.config.tiering.enabled is True
    assert state.config.tiering.long_prompt_tokens == 100
