"""Milestone J: tenant budgets & quotas.

- Unit: build_meter_keys cascade + config parsing
- Integration: org/team/user rejection, disabled no-op, 403/429 before rate limit
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import pytest
from pydantic import ValidationError

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.auth.models import IdentityContext
from meridian.config.models import BudgetConfig, MeridianConfig, ScopeBudget
from meridian.usage import build_meter_keys
from meridian.usage.bucket import period_bucket

KEY_ACME = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_TEAM = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
KEY_USER = "mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh"

NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Config ──────────────────────────────────────────────────────────────────


def test_budgets_default_disabled():
    cfg = MeridianConfig.from_dict({})
    assert cfg.budgets.enabled is False
    assert cfg.budgets.store == "sqlite"
    assert cfg.budgets.orgs == {}


def test_budgets_config_parses():
    cfg = MeridianConfig.from_dict({
        "budgets": {
            "enabled": True,
            "store": "memory",
            "orgs": {
                "acme": {
                    "daily": {"tokens": 1000, "requests": 50},
                },
            },
            "teams": {"acme/eng": {"daily": {"tokens": 100}}},
            "users": {"acme/alice": {"monthly": {"requests": 10}}},
        },
    })
    assert cfg.budgets.enabled is True
    assert cfg.budgets.store == "memory"
    assert cfg.budgets.orgs["acme"].daily.tokens == 1000
    assert cfg.budgets.teams["acme/eng"].daily.tokens == 100
    assert cfg.budgets.users["acme/alice"].monthly.requests == 10


def test_budgets_rejects_bad_store():
    with pytest.raises(ValidationError):
        BudgetConfig(store="redis")


# ── build_meter_keys ────────────────────────────────────────────────────────


def test_build_meter_keys_org_only():
    budgets = BudgetConfig(
        enabled=True,
        orgs={"acme": ScopeBudget.model_validate({"daily": {"tokens": 500, "requests": 10}})},
    )
    identity = IdentityContext(org_id="acme", team_id="eng", user_id="alice")
    keys = build_meter_keys(identity, budgets, now=NOW)
    assert len(keys) == 2
    assert {(k.metric, k.scope_level) for k in keys} == {
        ("tokens", "org"),
        ("requests", "org"),
    }
    assert all(k.period_bucket == period_bucket("daily", NOW) for k in keys)


def test_build_meter_keys_cascade_all_levels():
    budgets = BudgetConfig(
        enabled=True,
        orgs={"acme": ScopeBudget.model_validate({"daily": {"tokens": 1000}})},
        teams={"acme/eng": ScopeBudget.model_validate({"daily": {"tokens": 200}})},
        users={"acme/alice": ScopeBudget.model_validate({"monthly": {"requests": 5}})},
    )
    identity = IdentityContext(org_id="acme", team_id="eng", user_id="alice")
    keys = build_meter_keys(identity, budgets, now=NOW)
    levels = {(k.scope_level, k.scope_id, k.metric, k.period) for k in keys}
    assert levels == {
        ("org", "acme", "tokens", "daily"),
        ("team", "acme/eng", "tokens", "daily"),
        ("user", "acme/alice", "requests", "monthly"),
    }


def test_build_meter_keys_skips_missing_identity_fields():
    budgets = BudgetConfig(
        enabled=True,
        orgs={"acme": ScopeBudget.model_validate({"daily": {"tokens": 100}})},
        teams={"acme/eng": ScopeBudget.model_validate({"daily": {"tokens": 50}})},
        users={"acme/alice": ScopeBudget.model_validate({"daily": {"requests": 1}})},
    )
    # Org-level key only — no team/user on identity
    identity = IdentityContext(org_id="acme")
    keys = build_meter_keys(identity, budgets, now=NOW)
    assert len(keys) == 1
    assert keys[0].scope_level == "org"


def test_build_meter_keys_empty_when_no_matching_caps():
    budgets = BudgetConfig(enabled=True, orgs={"other": ScopeBudget.model_validate({"daily": {"tokens": 1}})})
    identity = IdentityContext(org_id="acme")
    assert build_meter_keys(identity, budgets, now=NOW) == []


# ── Integration ─────────────────────────────────────────────────────────────


async def _client(
    budgets: dict,
    *,
    rate_limit: Optional[dict] = None,
    keys: Optional[List[dict]] = None,
) -> httpx.AsyncClient:
    if keys is None:
        keys = [
            {"key": KEY_ACME, "org_id": "acme"},
            {"key": KEY_TEAM, "org_id": "acme", "team_id": "eng"},
            {"key": KEY_USER, "org_id": "acme", "team_id": "eng", "user_id": "alice"},
        ]
    cfg_dict: dict = {
        "auth": {"enabled": True, "keys": keys},
        "budgets": budgets,
        "backends": [{
            "name": "dead",
            "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock",
            "model": "demo",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    }
    if rate_limit is not None:
        cfg_dict["rate_limit"] = rate_limit
    cfg = MeridianConfig.from_dict(cfg_dict)
    await init_app(cfg, start_health=False)
    get_state().rate_limit.clear()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
    )


def _body(model: str = "demo", max_tokens: int = 1) -> dict:
    # Tiny max_tokens keeps request_ctx.cost small for request-count tests.
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": max_tokens,
    }


@pytest.mark.asyncio
async def test_budgets_disabled_is_noop():
    async with await _client({"enabled": False, "store": "memory"}) as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
            json=_body(),
        )
    assert resp.status_code == 502  # passed through to dead backend


@pytest.mark.asyncio
async def test_org_request_cap_blocks():
    budgets = {
        "enabled": True,
        "store": "memory",
        "orgs": {"acme": {"daily": {"requests": 1}}},
    }
    async with await _client(budgets) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert first.status_code == 502
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_exceeded"
    assert "org" in second.json()["error"]["message"]
    assert "Retry-After" in second.headers


@pytest.mark.asyncio
async def test_team_cap_blocks_independently():
    budgets = {
        "enabled": True,
        "store": "memory",
        "teams": {"acme/eng": {"daily": {"requests": 1}}},
    }
    async with await _client(budgets) as c:
        # Org-only key has no team → not subject to team cap
        org_resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
            json=_body(),
        )
        # Team key exhausts the team bucket
        h = {"Authorization": f"Bearer {KEY_TEAM}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert org_resp.status_code == 502
    assert first.status_code == 502
    assert second.status_code == 429
    assert "team" in second.json()["error"]["message"]


@pytest.mark.asyncio
async def test_user_cap_blocks():
    budgets = {
        "enabled": True,
        "store": "memory",
        "users": {"acme/alice": {"daily": {"requests": 1}}},
    }
    async with await _client(budgets) as c:
        h = {"Authorization": f"Bearer {KEY_USER}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert first.status_code == 502
    assert second.status_code == 429
    assert "user" in second.json()["error"]["message"]


@pytest.mark.asyncio
async def test_token_cap_blocks_on_cost():
    # Cap of 1 token-cost unit: any real request cost >> 1 with default weights.
    budgets = {
        "enabled": True,
        "store": "memory",
        "orgs": {"acme": {"daily": {"tokens": 1}}},
    }
    async with await _client(budgets) as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
            json=_body(max_tokens=256),
        )
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_model_access_403_does_not_spend_rate_limit():
    """403 access denial must precede rate limiting (no token spent)."""
    budgets = {"enabled": False, "store": "memory"}
    keys = [{"key": KEY_ACME, "org_id": "acme", "allowed_models": ["demo"]}]
    async with await _client(
        budgets,
        rate_limit={"enabled": True, "token_capacity": 1, "token_refill_rate": 0.001},
        keys=keys,
    ) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        denied = await c.post(
            "/v1/chat/completions", headers=h, json=_body(model="forbidden"),
        )
        # Same org still has its rate-limit token — allowed model passes limiter.
        allowed = await c.post(
            "/v1/chat/completions", headers=h, json=_body(model="demo"),
        )
    assert denied.status_code == 403
    assert allowed.status_code == 502  # not 429


@pytest.mark.asyncio
async def test_budget_429_does_not_spend_rate_limit():
    """Budget rejection must precede rate limiting (no token spent)."""
    budgets = {
        "enabled": True,
        "store": "memory",
        "orgs": {"acme": {"daily": {"requests": 1}}},
    }
    async with await _client(
        budgets,
        rate_limit={"enabled": True, "token_capacity": 1, "token_refill_rate": 0.001},
    ) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        budget_block = await c.post("/v1/chat/completions", headers=h, json=_body())
    # first spent the request budget and the rate token (passed budget, then RL).
    # second is budget-blocked; if it had also hit RL we'd still see 429 but
    # with different type/message. Assert budget message specifically.
    assert first.status_code == 502
    assert budget_block.status_code == 429
    assert "Budget exceeded" in budget_block.json()["error"]["message"]


@pytest.mark.asyncio
async def test_per_org_rate_limit_override():
    """rate_limit.org_overrides shrinks the burst for one org vs global default."""
    budgets = {"enabled": False, "store": "memory"}
    # Global rate limit is generous; acme override is tight.
    async with await _client(
        budgets,
        rate_limit={
            "enabled": True,
            "token_capacity": 100,
            "token_refill_rate": 100,
            "org_overrides": {
                "acme": {"token_capacity": 1, "token_refill_rate": 0.001},
            },
        },
    ) as c:
        h = {"Authorization": f"Bearer {KEY_ACME}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_body())
    assert first.status_code == 502
    assert second.status_code == 429
    assert second.json()["error"]["message"] == "Rate Limit Exceeded"
