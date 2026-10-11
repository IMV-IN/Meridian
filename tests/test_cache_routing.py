"""Cache locality, bounded state, feedback parsing, and baseline safety."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from meridian.config.models import BackendConfig, CacheAffinityConfig, MeridianConfig
from meridian.cost.extract import Usage, usage_from_dict, usage_from_sse_bytes
from meridian.registry.backend import Backend
from meridian.router.cache_key import prefix_keys
from meridian.router.prefix_index import PrefixIndex
from meridian.router.strategies import CacheAffinity, RequestContext, TokenAware, create_strategy


def body(text="x" * 1024, **overrides):
    return {"model": "m", "messages": [{"role": "user", "content": text}], **overrides}


def index(clock, **kwargs):
    return PrefixIndex(ttl_ms=100, max_entries=20, clock=lambda: clock[0], **kwargs)


def test_prefix_chain_and_prompt_namespace():
    keys = prefix_keys(body())
    assert keys == prefix_keys(body(temperature=.5, stream=True))
    assert set(keys) & set(prefix_keys(body("x" * 800 + "different")))
    assert not set(keys) & set(prefix_keys(body("different" + "x" * 1024)))
    assert not set(keys) & set(prefix_keys(body(model="other")))
    assert not set(keys) & set(prefix_keys(body(), scope="org:other"))
    assert not set(keys) & set(prefix_keys(body(tools=[{"type": "function", "function": {"name": "f"}}])))
    assert not set(keys) & set(prefix_keys(body(messages=[{"role": "system", "content": "x" * 1024}])))
    assert len(prefix_keys(body("x" * 100_000))) == 16
    assert all(len(k) == 32 and "xxx" not in k for k in keys)


@pytest.mark.parametrize("value", [None, {}, {"messages": []}, {"messages": [None]},
    {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}])
def test_unsupported_prompts_do_not_predict(value):
    assert prefix_keys(value) == ()


def test_index_longest_match_sliding_ttl_and_sweep():
    clock = [0]
    idx = index(clock)
    idx.record(["short"], "a", 0)
    idx.record(["long"], "b", 0)
    assert idx.lookup(["long", "short"]).backend == "b"
    clock[0] = 90
    assert idx.lookup(["long"]).trust == .5
    clock[0] = 110
    idx.sweep()
    assert idx.lookup(["short"]) is None
    assert idx.lookup(["long"]).backend == "b"
    clock[0] = 210
    assert idx.size() == 0


def test_index_evicts_nearest_expiry_and_corrects_misses():
    clock = [0]
    idx = PrefixIndex(ttl_ms=100, max_entries=2, clock=lambda: clock[0])
    idx.record(["a"], "a", 0)
    clock[0] = 1
    idx.record(["b"], "b", 0)
    idx.lookup(["a"])
    idx.record(["c"], "c", 0)
    assert idx.lookup(["b"]) is None
    assert idx.size() == 2
    for _ in range(5):
        idx.record(["a"], "a", 0)
    assert idx.lookup(["a"]) is None
    idx.record(["a"], "b", 0)
    idx.record(["a"], "b", 1)
    assert idx.lookup(["a"]).trust == pytest.approx(2 / 3)


def test_static_arm_does_not_correct_feedback():
    idx = index([0], feedback_correction=False)
    for _ in range(100):
        idx.record(["a"], "b", 0)
    assert idx.lookup(["a"]).trust == .5


def test_index_concurrent_updates_are_bounded():
    idx = index([0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: idx.record([str(n % 40)], "b", 1), range(1000)))
    assert idx.size() == 20


def backends():
    return [Backend(BackendConfig(name=name, url=f"http://{name}", model="m")) for name in ("a", "b")]


def test_cache_strategy_locality_overload_and_fallback():
    strategy = create_strategy("cache_affinity")
    assert isinstance(strategy, CacheAffinity)
    pool = backends()
    ctx = RequestContext(100, 10, 140, ("key",))
    assert strategy.select(pool, ctx) is TokenAware().select(pool, ctx)
    strategy.observe(ctx, "b", Usage(100, 10, 0, True))
    ctx = replace(ctx, cache_prediction=strategy.index.lookup(ctx.prefix_keys))
    assert strategy.select(pool, ctx).name == "b"
    pool[1].add_inflight_cost(10_000)
    assert strategy.select(pool, ctx).name == "a"
    assert strategy.select(pool, None) is TokenAware().select(pool, None)
    assert strategy.select([], ctx) is None


def test_zero_weight_exactly_matches_token_aware_with_telemetry():
    strategy = create_strategy("cache_affinity", queue_weight=7, mem_weight=13,
                               cache_affinity=CacheAffinityConfig(locality_weight=0))
    pool = backends()
    pool[0].queue_depth = 10
    pool[1].gpu_mem_util = .9
    strategy.index.record(["key"], "a", 1)
    ctx = RequestContext(10, 10, 50, ("key",), strategy.index.lookup(["key"]))
    assert strategy.select(pool, ctx) is TokenAware(queue_weight=7, mem_weight=13).select(pool, ctx)


def test_missing_cache_feedback_does_not_seed_or_decay_predictions():
    strategy = create_strategy("cache_affinity")
    ctx = RequestContext(10, 10, 50, ("key",))
    strategy.observe(ctx, "b", Usage(10, 10))
    assert strategy.index.size() == 0
    strategy.observe(ctx, "b", Usage(10, 10, 10, True))
    for _ in range(10):
        strategy.observe(ctx, "b", Usage(10, 10))
    assert strategy.index.lookup(ctx.prefix_keys).trust == .5


@pytest.mark.parametrize("cached,available", [(0, True), (8, True), (None, False),
    (-1, False), (11, False), (True, False), ("8", False)])
def test_usage_cache_details_and_nested_tail_fallback(cached, available):
    payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 2,
                         "prompt_tokens_details": {"cached_tokens": cached}}}
    expected = Usage(10, 2, cached if available else 0, available)
    assert usage_from_dict(payload) == expected
    raw = json.dumps(payload).encode()
    assert usage_from_sse_bytes(b"data: " + raw + b"\n\ndata: [DONE]\n\n") == expected
    assert usage_from_sse_bytes(raw[1:]) == expected  # truncated SSE tail


@pytest.mark.parametrize("kwargs", [{"block_chars": 0}, {"max_checkpoints": 0}, {"ttl_s": 0},
    {"max_entries": 0}, {"locality_weight": -1}, {"locality_weight": float("inf")}, {"min_trust": .5}])
def test_cache_config_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        MeridianConfig.from_dict({"gateway": {"cache_affinity": kwargs}})
