"""Full cross-repo chain: the real meridian-node agent drives meridian-control
over a real socket with mTLS enforced, and the gateway's ManagedProjectionSync
turns the node's Ready engine into a routable backend.

This is the end-to-end connection the three components exist for:
    node agent --mTLS--> meridian-control --projection--> gateway registry
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest

from meridian.api.state import build_backend, build_registry
from meridian.config.models import ControlPlaneConfig, MeridianConfig
from meridian.registry.managed import ManagedProjectionSync
from meridian_control.app import create_app
from meridian_control.config import ControlConfig

pytest.importorskip("meridian_node")
from meridian_node.agent import Agent  # noqa: E402
from meridian_node.config import Config  # noqa: E402
from meridian_node.control import ControlError  # noqa: E402
from meridian_node.http_transport import HttpTransport  # noqa: E402
from meridian_node.wiring import upgrade_transport  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_control(tmp_path):
    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp_path}/x.db", ca_dir=tmp_path / "ca",
        lease_ttl_seconds=30, require_mtls=True,
    )
    app = create_app(cfg)
    port = _free_port()
    server = uvicorn_server(app, port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def uvicorn_server(app, port):
    import uvicorn

    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))


def test_node_to_control_to_gateway(live_control, tmp_path):
    app, url = live_control
    svc = app.state.control_service
    token = svc.create_token(auto_approve=True)

    # Node enrolls, then must present its cert to act (mTLS gate).
    node_cfg = Config(control_plane_url=url, state_dir=tmp_path / "node", mode="observe-only")
    agent = Agent(node_cfg, HttpTransport(url, enrollment_token=token))
    node_id = agent.ensure_enrolled()
    with pytest.raises(ControlError) as exc:
        agent.establish_session()  # no cert presented yet
    assert exc.value.code == "NODE_NOT_AUTHORIZED"

    upgrade_transport(node_cfg, agent)
    agent.establish_session()
    agent.heartbeat_once()

    # The node reports a Ready engine with an endpoint + served model.
    agent.transport.post_observation(node_id, {
        "node_id": node_id, "observed_generation": 0,
        "agent_session_id": agent.session_id, "sequence": agent.state.last_sequence(),
        "engines": [{"engine_id": "engine_1", "phase": "Ready",
                     "endpoint": "https://127.0.0.1:8001", "model": "demo/model"}],
    })

    # Control's serving projection exposes it as routable.
    endpoints = httpx.get(f"{url}/admin/projection").json()["endpoints"]
    assert [e for e in endpoints if e["routable"]], endpoints

    # The gateway registers it as a routable backend for the served model.
    gcfg = MeridianConfig()
    registry = build_registry(gcfg)
    assert registry.eligible("demo/model") == []  # nothing before the sync
    sync = ManagedProjectionSync(
        registry, ControlPlaneConfig(enabled=True, url=url),
        build_backend=lambda bc: build_backend(gcfg, bc),
        client=httpx.AsyncClient(timeout=5.0),
    )
    asyncio.run(sync.sync_once())
    eligible = registry.eligible("demo/model")
    assert len(eligible) == 1
    assert eligible[0].name == f"managed:{node_id}:engine_1"
    assert eligible[0].url == "https://127.0.0.1:8001"
