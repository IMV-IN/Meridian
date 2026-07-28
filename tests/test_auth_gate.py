"""Integration test for the /v1/* API-key auth gate (middleware in main.py)."""

from __future__ import annotations

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import init_app
from meridian.config.models import MeridianConfig

KEY = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"


VIEWER_KEY = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"


def _cfg(enabled: bool) -> MeridianConfig:
    # No backends needed — the gate runs before routing. /v1/models with an
    # empty registry returns a 200 model list, which is enough to prove the
    # request passed the gate. KEY is a plain key (no ops-view right);
    # VIEWER_KEY has role=viewer for the ops-endpoint tests.
    return MeridianConfig.from_dict(
        {
            "auth": {
                "enabled": enabled,
                "keys": [
                    {"key": KEY, "org_id": "acme"},
                    {"key": VIEWER_KEY, "org_id": "acme", "role": "viewer"},
                ],
            }
        }
    )


async def _client(enabled: bool) -> httpx.AsyncClient:
    await init_app(_cfg(enabled), start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_gate_blocks_missing_header():
    async with await _client(enabled=True) as c:
        resp = await c.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_gate_blocks_unknown_key():
    async with await _client(enabled=True) as c:
        resp = await c.get("/v1/models", headers={"Authorization": "Bearer mrdn_ZZZZZZZZZZZZZZZZZZZZ"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_gate_allows_valid_key():
    async with await _client(enabled=True) as c:
        resp = await c.get("/v1/models", headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_gate_open_when_disabled():
    async with await _client(enabled=False) as c:
        resp = await c.get("/v1/models")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_always_open_even_when_enabled():
    async with await _client(enabled=True) as c:
        resp = await c.get("/metrics")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Ops endpoints (0.9.4): /meridian/status, /meridian/requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ops_endpoints_open_when_auth_disabled():
    async with await _client(enabled=False) as c:
        assert (await c.get("/meridian/status")).status_code == 200
        assert (await c.get("/meridian/requests")).status_code == 200


@pytest.mark.asyncio
async def test_ops_status_401_without_key_when_enabled():
    async with await _client(enabled=True) as c:
        resp = await c.get("/meridian/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ops_status_403_for_plain_key():
    async with await _client(enabled=True) as c:
        resp = await c.get("/meridian/status", headers={"Authorization": f"Bearer {KEY}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["type"] == "permission_error"


@pytest.mark.asyncio
async def test_ops_status_allows_viewer_key():
    async with await _client(enabled=True) as c:
        resp = await c.get(
            "/meridian/status", headers={"Authorization": f"Bearer {VIEWER_KEY}"}
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ops_requests_allows_viewer_key():
    async with await _client(enabled=True) as c:
        resp = await c.get(
            "/meridian/requests", headers={"Authorization": f"Bearer {VIEWER_KEY}"}
        )
    assert resp.status_code == 200
    assert "requests" in resp.json()


@pytest.mark.asyncio
async def test_ui_static_page_stays_open():
    # The static HTML bootstraps the browser; the data endpoints it calls are
    # what enforce auth. Serving the page itself must not require a key.
    async with await _client(enabled=True) as c:
        resp = await c.get("/ui")
    assert resp.status_code == 200
