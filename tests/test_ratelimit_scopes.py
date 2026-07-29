"""Phase 3 Track 5: per-model + global rate limits.

Scopes are checked global → model → org/ip; only when *all* admit does each
consume a token, so a hot tenant can never burn the fleet-wide bucket with
requests its own bucket would reject.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from prometheus_client import REGISTRY

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import MeridianConfig

KEY_ACME = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_GLOBEX = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cfg(**rate_limit) -> MeridianConfig:
    return MeridianConfig.from_dict({
        "rate_limit": {"enabled": True, **rate_limit},
        "auth": {"enabled": True, "keys": [
            {"key": KEY_ACME, "org_id": "acme"},
            {"key": KEY_GLOBEX, "org_id": "globex"},
        ]},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })


async def _client(cfg: MeridianConfig) -> httpx.AsyncClient:
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=meridian_app), base_url="http://test")


def _body(model: str = "demo") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def _scope_count(scope: str) -> float:
    for family in REGISTRY.collect():
        if family.name == "meridian_ratelimit_rejections":
            for sample in family.samples:
                if sample.labels.get("scope") == scope:
                    return sample.value
    return 0.0


# ── Config shape ───────────────────────────────────────────────────────────


class TestConfigShape:
    def test_global_alias_loads(self) -> None:
        cfg = MeridianConfig.from_dict({
            "rate_limit": {
                "enabled": True,
                "global": {"token_capacity": 100, "token_refill_rate": 50},
            },
        })
        assert cfg.rate_limit.global_limit is not None
        assert cfg.rate_limit.global_limit.token_capacity == 100

    def test_models_map_loads(self) -> None:
        cfg = MeridianConfig.from_dict({
            "rate_limit": {"enabled": True, "models": {"gpt-x": {"token_capacity": 3}}},
        })
        assert cfg.rate_limit.models["gpt-x"].token_capacity == 3

    def test_defaults_unset(self) -> None:
        cfg = MeridianConfig()
        assert cfg.rate_limit.global_limit is None
        assert cfg.rate_limit.models == {}


# ── Global scope ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_bucket_shared_across_orgs():
    """One fleet-wide bucket: acme's request leaves 0, globex is rejected too."""
    before = _scope_count("global")
    async with await _client(_cfg(
        token_capacity=10, token_refill_rate=10,
        global_limit={"token_capacity": 2, "token_refill_rate": 0.001},
    )) as c:
        a1 = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_ACME}"}, json=_body())
        a2 = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_ACME}"}, json=_body())
        b = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_GLOBEX}"}, json=_body())
    assert a1.status_code == 502
    assert a2.status_code == 502
    assert b.status_code == 429          # global bucket spent by acme
    assert _scope_count("global") - before == 1.0


# ── Model scope ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_bucket_by_model_id():
    before = _scope_count("model")
    async with await _client(_cfg(
        token_capacity=10, token_refill_rate=10,
        models={"demo": {"token_capacity": 1, "token_refill_rate": 0.001}},
    )) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert first.status_code == 502
    assert second.status_code == 429     # demo's bucket exhausted
    assert _scope_count("model") - before == 1.0


@pytest.mark.asyncio
async def test_model_does_not_block_other_orgs_org_scope():
    """Model bucket capacity is shared per model across orgs (queueing standard)."""
    async with await _client(_cfg(
        token_capacity=10, token_refill_rate=10,
        models={"demo": {"token_capacity": 1, "token_refill_rate": 0.001}},
    )) as c:
        a = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_ACME}"}, json=_body())
        b = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY_GLOBEX}"}, json=_body())
    assert a.status_code == 502
    assert b.status_code == 429  # same model → same bucket, org doesn't matter


# ── No cross-scope consumption ────────────────────────────────


@pytest.mark.asyncio
async def test_model_rejection_spares_global_bucket():
    """M4: a request rejected at the model scope must not burn the global or
    org tokens — check-then-consume holds for the MIDDLE scope too."""
    cfg = MeridianConfig.from_dict({
        "rate_limit": {
            "enabled": True,
            "token_capacity": 2, "token_refill_rate": 0.001,
            "global_limit": {"token_capacity": 2, "token_refill_rate": 0.001},
            "models": {"demo": {"token_capacity": 1, "token_refill_rate": 0.001}},
        },
        "auth": {"enabled": True, "keys": [{"key": KEY_ACME, "org_id": "acme"}]},
        "backends": [{
            # Empty model string: serves any request model — lets us exercise
            # an unmetered model id without another backend.
            "name": "dead", "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock", "model": "", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    async with await _client(cfg) as c:
        ha = {"Authorization": f"Bearer {KEY_ACME}"}
        a1 = await c.post("/v1/chat/completions", headers=ha, json=_body())
        # demo model bucket now empty — global and org must NOT be touched
        # by this rejection for the reachability check below to work.
        a2 = await c.post("/v1/chat/completions", headers=ha, json=_body())
        # Org+global each spent exactly 1 (the admitted request) so acme can
        # still pass both on a different (unmetered) model.
        a3 = await c.post("/v1/chat/completions", headers=ha, json=_body("other-model"))
    assert a1.status_code == 502
    assert a2.status_code == 429          # model scope rejects
    assert a3.status_code == 502          # global+org tokens survived the rejection


@pytest.mark.asyncio
async def test_org_rejection_spares_global_bucket():
    """Org hot loop: every org-limited request must NOT burn global tokens."""
    before = _scope_count("org")
    async with await _client(_cfg(
        token_capacity=1, token_refill_rate=0.001,
        global_limit={"token_capacity": 2, "token_refill_rate": 0.001},
    )) as c:
        ha = {"Authorization": f"Bearer {KEY_ACME}"}
        hb = {"Authorization": f"Bearer {KEY_GLOBEX}"}
        a1 = await c.post("/v1/chat/completions", headers=ha, json=_body())
        # acme's own bucket is empty — this MUST 429 without touching global.
        a2 = await c.post("/v1/chat/completions", headers=ha, json=_body())
        # globex has its own org bucket; global still has its 2nd token.
        b1 = await c.post("/v1/chat/completions", headers=hb, json=_body())
    assert a1.status_code == 502
    assert a2.status_code == 429
    assert b1.status_code == 502          # global token survived acme's spam
    assert _scope_count("org") - before == 1.0


@pytest.mark.asyncio
async def test_retry_after_header_format():
    async with await _client(_cfg(
        token_capacity=1, token_refill_rate=0.5,
    )) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        await c.post("/v1/chat/completions", headers=h, json=_body())
        resp = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "2.0"  # 1 / 0.5, unchanged format
