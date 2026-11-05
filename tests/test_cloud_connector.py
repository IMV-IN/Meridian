"""Gateway ↔ Meridian Cloud connector, end to end in-process.

A real cloud-mode meridian-control app and a real gateway AppState talk over
httpx.ASGITransport: token-authenticated projection pull, metadata-only status
reports, and workspace isolation.
"""

from __future__ import annotations

import base64
import json
import re

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from meridian.api.cloud_report import CloudReporter  # noqa: E402
from meridian.api.main import init_app  # noqa: E402
from meridian.api.state import build_backend  # noqa: E402
from meridian.config.models import MeridianConfig  # noqa: E402
from meridian.registry.managed import ManagedProjectionSync  # noqa: E402
from meridian_control.app import create_app  # noqa: E402
from meridian_control.config import ControlConfig  # noqa: E402

CSRF = {"X-Meridian-CSRF": "1"}
API_KEY = "mrdn_CloudConnectorTestKey0000001"


def _sign_in(control: TestClient, outbox, email: str) -> None:
    control.post("/auth/email/start", json={"email": email}, headers=CSRF)
    msg = json.loads(sorted(outbox.iterdir())[-1].read_text())
    code = re.search(r"\b(\d{6})\b", msg["text"]).group(1)
    assert control.post("/auth/email/verify", json={"email": email, "code": code}, headers=CSRF).status_code == 200


def _pub() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _ready_node(control: TestClient, ws: str, endpoint: str) -> str:
    token = control.post(f"/api/w/{ws}/tokens", json={"auto_approve": True}, headers=CSRF).json()["token"]
    node_id = control.post(
        "/control/v1/enroll", headers={"Authorization": f"Bearer {token}"}, json={"node_public_key": _pub()}
    ).json()["node_id"]
    control.post(f"/control/v1/nodes/{node_id}/sessions", json={"agent_session_id": "s1"})
    control.post(
        f"/control/v1/nodes/{node_id}/observations",
        json={"node_id": node_id, "sequence": 1, "observed_generation": 0,
              "engines": [{"engine_id": "e1", "phase": "Ready", "endpoint": endpoint, "model": "m"}]},
    )
    return node_id


@pytest.mark.asyncio
async def test_gateway_pulls_scoped_projection_and_reports(tmp_path):
    outbox = tmp_path / "outbox"
    control_app = create_app(
        ControlConfig(
            db_url=f"sqlite:///{tmp_path}/cloud.db", ca_dir=tmp_path / "ca", cloud=True, dev_mail_outbox=str(outbox)
        )
    )
    control = TestClient(control_app)
    _sign_in(control, outbox, "owner@acme.com")
    ws = control.post("/api/workspaces", json={"name": "Acme"}, headers=CSRF).json()["id"]
    other_ws = control.post("/api/workspaces", json={"name": "Elsewhere"}, headers=CSRF).json()["id"]
    node_id = _ready_node(control, ws, "http://10.0.0.5:8000")
    _ready_node(control, other_ws, "http://10.9.9.9:8000")  # must never reach this gateway
    token = control.post(f"/api/w/{ws}/gateway-tokens", json={"name": "edge"}, headers=CSRF).json()["token"]

    cfg = MeridianConfig.from_dict({
        "backends": [{"name": "static-a", "url": "http://127.0.0.1:9", "model": "m"}],
        "auth": {"enabled": True, "keys": [{"key": API_KEY, "org_id": "acme"}]},
        "control_plane": {
            "enabled": True, "url": "http://control", "token": token,
            "gateway_id": "gw-test", "gateway_name": "Test gateway",
        },
    })
    state = await init_app(cfg, start_health=False)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=control_app), base_url="http://control")
    try:
        sync = ManagedProjectionSync(
            state.registry, cfg.control_plane, build_backend=lambda bc: build_backend(cfg, bc), client=client
        )
        await sync.sync_once()
        names = {b.name for b in state.registry.all_backends()}
        assert names == {"static-a", f"managed:{node_id}:e1"}  # other workspace's engine excluded

        reporter = CloudReporter(state, client=client)
        assert await reporter.send_once() is True
        state.record_request("mrdn-1", "m", False, "static-a", 200, 42.0, None, org_id="acme")
        state.record_request("mrdn-2", "m", True, "static-a", 502, 900.0, "upstream_5xx", org_id="acme")
        assert await reporter.send_once() is True

        gateways = control.get(f"/api/w/{ws}/gateways").json()["gateways"]
        assert [g["gateway_id"] for g in gateways] == ["gw-test"]
        g = gateways[0]
        assert g["name"] == "Test gateway" and g["online"] is True and g["backends_total"] == 2
        report = control.get(f"/api/w/{ws}/gateways/gw-test").json()["report"]
        assert {"status", "window", "totals", "recent_requests"} <= set(report)
        assert report["recent_requests"][0]["request_id"] == "mrdn-2"
        assert "rps" in report["window"]
        raw = json.dumps(report)
        assert API_KEY not in raw and token not in raw  # metadata only, no secrets
        assert control.get(f"/api/w/{other_ws}/gateways").json()["gateways"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_without_token_the_legacy_projection_path_is_used():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"endpoints": []})

    cfg = MeridianConfig.from_dict({"control_plane": {"enabled": True, "url": "http://control:8443"}})
    state = await init_app(cfg, start_health=False)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sync = ManagedProjectionSync(
        state.registry, cfg.control_plane, build_backend=lambda bc: build_backend(cfg, bc), client=client
    )
    await sync.sync_once()
    assert seen[0].url.path == "/admin/projection"
    assert "authorization" not in seen[0].headers
    reporter = CloudReporter(state, client=client)
    assert await reporter.send_once() is False  # nothing to send without a token
    await client.aclose()


def test_token_from_environment(monkeypatch):
    monkeypatch.setenv("MERIDIAN_CLOUD_TOKEN", "mgw_from_env")
    cfg = MeridianConfig.from_dict({"control_plane": {"enabled": True, "url": "https://x"}})
    assert cfg.control_plane.resolved_token() == "mgw_from_env"


@pytest.mark.asyncio
async def test_default_gateway_identity_includes_the_port(monkeypatch):
    """Two gateways on one host must not overwrite each other's reports."""
    from meridian.api import cloud_report

    monkeypatch.setattr(cloud_report.socket, "gethostname", lambda: "gpu box #1")
    assert cloud_report.default_gateway_id(8080) == "gpu-box-1:8080"
    assert cloud_report.default_gateway_id(8081) != cloud_report.default_gateway_id(8080)
    assert cloud_report.default_gateway_id() == "gpu-box-1"

    cfg = MeridianConfig.model_validate({
        "gateway": {"port": 9090},
        "backends": [{"name": "b", "url": "http://127.0.0.1:1"}],
        "control_plane": {"enabled": True, "url": "http://cloud", "token": "mgw_x"},
    })
    state = await init_app(cfg, start_health=False)
    report = CloudReporter(state, client=None).build_report()
    assert report["gateway_id"] == "gpu-box-1:9090"
    assert report["name"] == "gpu-box-1:9090"
