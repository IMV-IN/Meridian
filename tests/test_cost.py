"""Milestone M: usage extract, ledger, and enterprise authz on usage APIs."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from meridian.api.errors import GatewayError
from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.auth.models import IdentityContext
from meridian.config.models import MeridianConfig
from meridian.cost.authz import clamp_window_days, resolve_usage_scope
from meridian.cost.extract import compute_cost, usage_from_dict, usage_from_sse_bytes
from meridian.cost.ledger import InMemoryCostLedger
from meridian.cost.record import record_actual_usage

KEY_ACME = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_GLOBEX = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
KEY_ADMIN = "mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh"
KEY_TEAM = "mrdn_2Bb3Cc4Dd5Ee6Ff7Gg8Hh9Ii"


def test_usage_from_dict():
    assert usage_from_dict({"usage": {"prompt_tokens": 3, "completion_tokens": 7}}) == (3, 7)
    assert usage_from_dict({}) is None


def test_usage_from_sse_last_wins():
    early = {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    late = {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    raw = (
        f"data: {json.dumps(early)}\n\n"
        f"data: {json.dumps(late)}\n\n"
        "data: [DONE]\n\n"
    ).encode()
    assert usage_from_sse_bytes(raw) == (10, 5)


def test_compute_cost():
    assert compute_cost(1_000_000, 500_000, prompt_per_1m=1.0, completion_per_1m=2.0) == 2.0


def test_ledger_accumulates():
    led = InMemoryCostLedger()
    led.record(org_id="a", team_id="t", model="m", prompt_tokens=10, completion_tokens=5, cost=0.01)
    led.record(org_id="a", team_id="t", model="m", prompt_tokens=10, completion_tokens=5, cost=0.01)
    rows = led.query(org_id="a")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 20
    assert rows[0].requests == 2


def test_resolve_scope_non_admin_forced_org():
    idn = IdentityContext(org_id="acme", team_id=None)
    o, t = resolve_usage_scope(idn, None, None)
    assert o == "acme" and t is None
    with pytest.raises(GatewayError) as ei:
        resolve_usage_scope(idn, "globex", None)
    assert ei.value.status == 403


def test_resolve_scope_team_key_forced():
    idn = IdentityContext(org_id="acme", team_id="eng")
    o, t = resolve_usage_scope(idn, None, None)
    assert (o, t) == ("acme", "eng")
    with pytest.raises(GatewayError):
        resolve_usage_scope(idn, "acme", "sales")


def test_resolve_scope_admin():
    idn = IdentityContext(org_id="finance", cost_admin=True)
    assert resolve_usage_scope(idn, None, None) == (None, None)
    assert resolve_usage_scope(idn, "acme", "eng") == ("acme", "eng")


def test_clamp_window():
    assert clamp_window_days(0, 30) == 1
    assert clamp_window_days(9999, 366) == 366
    assert clamp_window_days(7, 366) == 7


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _auth_cfg(**cost_extra):
    cost = {"enabled": True, "store": "memory", **cost_extra}
    return MeridianConfig.from_dict({
        "auth": {
            "enabled": True,
            "keys": [
                {"key": KEY_ACME, "org_id": "acme"},
                {"key": KEY_GLOBEX, "org_id": "globex"},
                {"key": KEY_ADMIN, "org_id": "ops", "cost_admin": True},
                {"key": KEY_TEAM, "org_id": "acme", "team_id": "eng"},
            ],
        },
        "cost": cost,
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })


@pytest.mark.asyncio
async def test_cost_disabled_empty_report_no_auth():
    cfg = MeridianConfig.from_dict({
        "cost": {"enabled": False},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get("/meridian/usage")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


@pytest.mark.asyncio
async def test_usage_requires_auth_when_cost_on():
    await init_app(_auth_cfg(), start_health=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get("/meridian/usage")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_usage_requires_auth_even_if_auth_config_off():
    """cost on + auth off → still 401 (refuse open export)."""
    cfg = MeridianConfig.from_dict({
        "auth": {"enabled": False},
        "cost": {"enabled": True, "store": "memory"},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get("/meridian/usage")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_sees_only_own_org():
    await init_app(_auth_cfg(), start_health=False)
    st = get_state()
    record_actual_usage(st, model="demo", org_id="acme", team_id="eng", prompt_tokens=5, completion_tokens=1)
    record_actual_usage(st, model="demo", org_id="globex", team_id="", prompt_tokens=9, completion_tokens=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
        )
        assert r.status_code == 200
        orgs = {row["org_id"] for row in r.json()["rows"]}
        assert orgs == {"acme"}

        forbidden = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
            params={"org": "globex"},
        )
        assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_admin_sees_all_orgs():
    await init_app(_auth_cfg(), start_health=False)
    st = get_state()
    record_actual_usage(st, model="demo", org_id="acme", team_id="", prompt_tokens=1, completion_tokens=1)
    record_actual_usage(st, model="demo", org_id="globex", team_id="", prompt_tokens=1, completion_tokens=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_ADMIN}"},
        )
        assert r.status_code == 200
        orgs = {row["org_id"] for row in r.json()["rows"]}
        assert orgs == {"acme", "globex"}


@pytest.mark.asyncio
async def test_csv_requires_auth_and_scopes():
    await init_app(_auth_cfg(), start_health=False)
    st = get_state()
    record_actual_usage(st, model="demo", org_id="acme", team_id="", prompt_tokens=2, completion_tokens=2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        bare = await c.get("/meridian/usage.csv")
        assert bare.status_code == 401
        ok = await c.get(
            "/meridian/usage.csv",
            headers={"Authorization": f"Bearer {KEY_ACME}"},
        )
        assert ok.status_code == 200
        assert "acme" in ok.text
        assert "prompt_tokens" in ok.text
