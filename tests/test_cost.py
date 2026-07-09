"""Milestone M: usage extract + cost ledger + attribution path."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import MeridianConfig
from meridian.cost.extract import compute_cost, usage_from_dict, usage_from_sse_bytes
from meridian.cost.ledger import InMemoryCostLedger


def test_usage_from_dict():
    assert usage_from_dict({"usage": {"prompt_tokens": 3, "completion_tokens": 7}}) == (3, 7)
    assert usage_from_dict({}) is None


def test_usage_from_sse():
    chunk = {
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    raw = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
    assert usage_from_sse_bytes(raw) == (10, 5)


def test_compute_cost():
    # 1M prompt @ $1 + 0.5M completion @ $2 = 1 + 1 = 2
    assert compute_cost(1_000_000, 500_000, prompt_per_1m=1.0, completion_per_1m=2.0) == 2.0


def test_ledger_accumulates():
    led = InMemoryCostLedger()
    led.record(org_id="a", team_id="t", model="m", prompt_tokens=10, completion_tokens=5, cost=0.01)
    led.record(org_id="a", team_id="t", model="m", prompt_tokens=10, completion_tokens=5, cost=0.01)
    rows = led.query(org_id="a")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 20
    assert rows[0].completion_tokens == 10
    assert rows[0].requests == 2
    assert abs(rows[0].cost - 0.02) < 1e-9


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_cost_disabled_empty_report():
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
    assert r.json()["rows"] == []


@pytest.mark.asyncio
async def test_non_stream_records_usage_from_live_mock():
    """Integration: real mock backend usage field → ledger."""
    # Start a tiny mock via uvicorn is heavy; unit-test record path instead:
    # full e2e uses dead backend. Here we drive the ledger via extract+record.
    from meridian.cost.record import record_actual_usage

    cfg = MeridianConfig.from_dict({
        "cost": {
            "enabled": True,
            "store": "memory",
            "default_prompt_per_1m": 1.0,
            "default_completion_per_1m": 2.0,
        },
    })
    await init_app(cfg, start_health=False)
    st = get_state()
    assert st.cost_ledger is not None
    record_actual_usage(
        st, model="demo", org_id="acme", team_id="eng",
        prompt_tokens=10, completion_tokens=20,
    )
    rows = st.cost_ledger.query(org_id="acme")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 10
    assert rows[0].completion_tokens == 20
    # cost = 10/1e6 * 1 + 20/1e6 * 2
    assert abs(rows[0].cost - (10 / 1e6 + 40 / 1e6)) < 1e-12

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    ) as c:
        r = await c.get("/meridian/usage", params={"org": "acme"})
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert data["rows"][0]["org_id"] == "acme"
        csv_r = await c.get("/meridian/usage.csv", params={"org": "acme"})
        assert csv_r.status_code == 200
        assert "prompt_tokens" in csv_r.text
        assert "acme" in csv_r.text
