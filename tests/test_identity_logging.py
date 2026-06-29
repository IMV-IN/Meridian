"""Milestone G: identity (org_id/team_id) flows into the JSONL request log."""

from __future__ import annotations

import json
import os
import socket
import tempfile

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import init_app
from meridian.config.models import MeridianConfig
from meridian.metrics.logger import RequestLogger

KEY = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"


# ── Unit: the logger writes the new identity fields (null by default) ────────


def test_logger_writes_identity_fields():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "r.jsonl")
        lg = RequestLogger(path)
        lg.log(request_id="x", model="m", stream=False, backend="b",
               status_code=200, latency_ms=1.0, org_id="acme", team_id="eng")
        lg.log(request_id="y", model="m", stream=False, backend="b",
               status_code=200, latency_ms=1.0)  # no identity
        lg.close()
        rows = [json.loads(line) for line in open(path)]
    assert rows[0]["org_id"] == "acme" and rows[0]["team_id"] == "eng"
    assert rows[1]["org_id"] is None and rows[1]["team_id"] is None


# ── Integration: authed request → identity in the log line ──────────────────


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # closed once the socket is released


@pytest.mark.asyncio
async def test_authed_request_logs_org_and_team():
    jsonl = os.path.join(tempfile.mkdtemp(), "requests.jsonl")
    cfg = MeridianConfig.from_dict({
        "logging": {"level": "INFO", "jsonl_path": jsonl},
        "auth": {"enabled": True, "keys": [
            {"key": KEY, "org_id": "acme", "team_id": "eng", "user_id": "alice"},
        ]},
        "backends": [{
            "name": "dead", "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock", "model": "demo", "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)

    transport = httpx.ASGITransport(app=meridian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Backend is dead → 502, but the finally block still logs the request.
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json={"model": "demo", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 502

    rows = [json.loads(line) for line in open(jsonl)]
    assert rows[-1]["org_id"] == "acme"
    assert rows[-1]["team_id"] == "eng"
