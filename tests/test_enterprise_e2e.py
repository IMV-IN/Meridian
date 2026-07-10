"""0.9.3 enterprise e2e: auth + budgets + cost + stream/non-stream + failover.

Self-contained: starts mock backend(s) in-process, re-inits Meridian per scenario.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import httpx
import pytest
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import MeridianConfig
from mock_backend.server import app as mock_app

KEY_APP = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_ADMIN = "mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh"
KEY_OPS = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_mock(port: int) -> None:
    cfg = uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=0.5)
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"mock not up on {port}")


_MOCK_PORT = _port()
_start_mock(_MOCK_PORT)
_MOCK = f"http://127.0.0.1:{_MOCK_PORT}"


def _base_cfg(
    *,
    backends: Optional[List[Dict[str, Any]]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if backends is None:
        backends = [{
            "name": "mock-a",
            "url": _MOCK,
            "engine": "mock",
            "model": "demo-model",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }]
    cfg: Dict[str, Any] = {
        "gateway": {
            "strategy": "least_inflight",
            "prefill_weight": 1.0,
            "decode_weight": 4.0,
            "default_max_tokens": 16,
        },
        "health": {
            "interval_s": 3600,
            "timeout_s": 1,
            "fail_threshold": 1,
            "success_threshold": 1,
        },
        "auth": {
            "enabled": True,
            "keys": [
                {"key": KEY_APP, "org_id": "acme", "team_id": "eng"},
                {"key": KEY_ADMIN, "org_id": "ops", "cost_admin": True},
                {"key": KEY_OPS, "org_id": "ops", "ops_admin": True},
            ],
        },
        "budgets": {
            "enabled": True,
            "store": "memory",
            "orgs": {
                "acme": {
                    "daily": {"tokens": 1_000_000, "requests": 10_000},
                }
            },
        },
        "cost": {
            "enabled": True,
            "store": "memory",
            "default_prompt_per_1m": 1.0,
            "default_completion_per_1m": 2.0,
        },
        "backends": backends,
    }
    if extra:
        cfg.update(extra)
    return cfg


async def _client(cfg_dict: Dict[str, Any]) -> httpx.AsyncClient:
    await init_app(MeridianConfig.from_dict(cfg_dict), start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
        timeout=30.0,
    )


def _chat_body(*, stream: bool = False, content: str = "enterprise e2e") -> dict:
    return {
        "model": "demo-model",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8,
        "stream": stream,
    }


@pytest.mark.asyncio
async def test_auth_required_on_v1():
    async with await _client(_base_cfg()) as c:
        r = await c.post("/v1/chat/completions", json=_chat_body())
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_non_stream_auth_budget_cost_headers():
    async with await _client(_base_cfg()) as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_APP}"},
            json=_chat_body(),
        )
        assert r.status_code == 200
        assert r.headers.get("x-request-id", "").startswith("mrdn-")
        assert r.headers.get("x-meridian-backend") == "mock-a"
        assert "x-meridian-budget-remaining-tokens" in r.headers
        assert "x-meridian-budget-remaining-requests" in r.headers
        rem_tok = float(r.headers["x-meridian-budget-remaining-tokens"])
        rem_req = float(r.headers["x-meridian-budget-remaining-requests"])
        assert rem_tok < 1_000_000
        assert rem_req == 9999.0  # 10000 - 1

        usage = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_APP}"},
        )
        assert usage.status_code == 200
        body = usage.json()
        assert body["enabled"] is True
        assert any(row["org_id"] == "acme" for row in body["rows"])


@pytest.mark.asyncio
async def test_stream_ends_with_done_and_headers():
    async with await _client(_base_cfg()) as c:
        async with c.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_APP}"},
            json=_chat_body(stream=True),
        ) as r:
            assert r.status_code == 200
            assert r.headers.get("x-meridian-backend")
            assert "x-meridian-budget-remaining-tokens" in r.headers
            chunks: List[str] = []
            async for line in r.aiter_lines():
                if line:
                    chunks.append(line)
            assert any(line == "data: [DONE]" for line in chunks)
            assert len(chunks) > 1


@pytest.mark.asyncio
async def test_budget_exhaustion_429():
    cfg = _base_cfg()
    cfg["budgets"]["orgs"]["acme"]["daily"] = {"requests": 1, "tokens": 1_000_000}
    async with await _client(cfg) as c:
        h = {"Authorization": f"Bearer {KEY_APP}"}
        first = await c.post("/v1/chat/completions", headers=h, json=_chat_body())
        second = await c.post("/v1/chat/completions", headers=h, json=_chat_body())
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_exceeded"
    assert "Retry-After" in second.headers


@pytest.mark.asyncio
async def test_cost_admin_cross_org_usage():
    async with await _client(_base_cfg()) as c:
        await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_APP}"},
            json=_chat_body(content="bill me"),
        )
        denied = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_APP}"},
            params={"org": "other"},
        )
        assert denied.status_code == 403
        ok = await c.get(
            "/meridian/usage",
            headers={"Authorization": f"Bearer {KEY_ADMIN}"},
        )
        assert ok.status_code == 200
        assert any(row["org_id"] == "acme" for row in ok.json()["rows"])


@pytest.mark.asyncio
async def test_failover_to_healthy_backend():
    dead = _port()
    backends = [
        {
            "name": "dead",
            "url": f"http://127.0.0.1:{dead}",
            "engine": "mock",
            "model": "demo-model",
            "weight": 1,
            "health_endpoint": "/v1/models",
        },
        {
            "name": "live",
            "url": _MOCK,
            "engine": "mock",
            "model": "demo-model",
            "weight": 1,
            "health_endpoint": "/v1/models",
        },
    ]
    async with await _client(_base_cfg(backends=backends)) as c:
        st = get_state()
        dead_b = st.registry.get("dead")
        live_b = st.registry.get("live")
        assert dead_b is not None and live_b is not None
        dead_b.healthy = False
        live_b.healthy = True

        r = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY_APP}"},
            json=_chat_body(),
        )
        assert r.status_code == 200
        assert r.headers.get("x-meridian-backend") == "live"


@pytest.mark.asyncio
async def test_version_and_status_open():
    async with await _client(_base_cfg()) as c:
        ver = await c.get("/meridian/version")
        assert ver.status_code == 200
        assert "version" in ver.json()
        st = await c.get("/meridian/status")
        assert st.status_code == 200
        assert "backends" in st.json()
