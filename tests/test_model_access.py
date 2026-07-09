"""Milestone I: per-key model allow-list (403 on disallowed model)."""

from __future__ import annotations

import socket

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import init_app
from meridian.auth import build_key_index
from meridian.config.models import AuthConfig, KeyConfig, MeridianConfig

KEY = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"


# ── Unit: build_key_index populates allowed_models ───────────────────────────


def test_allowed_models_on_identity():
    idx = build_key_index(AuthConfig(enabled=True, keys=[
        KeyConfig(key=KEY, org_id="acme", allowed_models=["demo", "small"]),
    ]))
    assert idx[KEY].allowed_models == frozenset({"demo", "small"})


def test_empty_allowed_models_is_unrestricted():
    idx = build_key_index(AuthConfig(enabled=True, keys=[
        KeyConfig(key=KEY, org_id="acme"),
    ]))
    assert idx[KEY].allowed_models == frozenset()


# ── Integration: the 403 gate ───────────────────────────────────────────────


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _client(allowed: list[str], *, auth_enabled: bool = True) -> httpx.AsyncClient:
    cfg = MeridianConfig.from_dict({
        "auth": {"enabled": auth_enabled, "keys": [
            {"key": KEY, "org_id": "acme", "allowed_models": allowed},
        ]},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=meridian_app), base_url="http://test")


def _post(c: httpx.AsyncClient, model: str):
    return c.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )


@pytest.mark.asyncio
async def test_allowed_model_passes_gate():
    async with await _client(["demo"]) as c:
        resp = await _post(c, "demo")
    assert resp.status_code == 502  # passed the gate, upstream dead


@pytest.mark.asyncio
async def test_disallowed_model_403():
    async with await _client(["demo"]) as c:
        resp = await _post(c, "forbidden")
    assert resp.status_code == 403
    assert resp.json()["error"]["type"] == "permission_error"


@pytest.mark.asyncio
async def test_empty_allow_list_is_unrestricted():
    # No allow-list => gate skipped; served model routes through to 502.
    async with await _client([]) as c:
        resp = await _post(c, "demo")
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_auth_disabled_no_gate():
    # allowed=["demo"] but auth off => identity is None => no gate fires.
    # "forbidden" isn't served, so it 503s at routing — the point is it is
    # never 403'd by the access gate.
    async with await _client(["demo"], auth_enabled=False) as c:
        resp = await _post(c, "forbidden")
    assert resp.status_code != 403
