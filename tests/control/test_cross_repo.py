"""Cross-repository compatibility: the real meridian-node agent + HttpTransport
drive the real meridian-control app over a real HTTP socket (DESIGN.md 20, 24).

This is the closed loop the two repos were designed for: the control service
runs under uvicorn on an ephemeral port, and the node agent's own transport
speaks to it. Skipped if meridian-node is not installed.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from meridian_control.app import create_app
from meridian_control.config import ControlConfig

pytest.importorskip("meridian_node")
from meridian_node.agent import Agent  # noqa: E402
from meridian_node.config import Config  # noqa: E402
from meridian_node.control import ControlError  # noqa: E402
from meridian_node.http_transport import HttpTransport  # noqa: E402


@pytest.fixture
def live(tmp_path):
    import uvicorn

    cfg = ControlConfig(db_url=f"sqlite:///{tmp_path}/x.db", ca_dir=tmp_path / "ca", lease_ttl_seconds=30)
    app = create_app(cfg)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "control server did not start"
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _agent(tmp_path, url, token, mode="observe-only"):
    transport = HttpTransport(url, enrollment_token=token)
    node_cfg = Config(control_plane_url=url, state_dir=tmp_path / "node", mode=mode)
    return Agent(node_cfg, transport), transport


def test_enroll_session_heartbeat_across_repos(live, tmp_path):
    app, url = live
    token = app.state.control_service.create_token(auto_approve=True)
    agent, _ = _agent(tmp_path, url, token)

    node_id = agent.ensure_enrolled()
    from meridian_control.models import Node
    with app.state.control_service._sf() as s:
        assert s.get(Node, node_id) is not None  # persisted in the durable store
    agent.establish_session()
    resp = agent.heartbeat_once()
    assert resp["accepted_sequence"] == 1
    assert resp["desired_generation"] == 0


def test_pending_approval_across_repos(live, tmp_path):
    app, url = live
    svc = app.state.control_service
    token = svc.create_token(auto_approve=False)
    agent, _ = _agent(tmp_path, url, token)

    with pytest.raises(ControlError):
        agent.ensure_enrolled(max_polls=2)  # not approved yet

    from meridian_control.models import Claim
    with svc._sf() as s:
        claim_id = s.query(Claim).first().claim_id
    svc.approve_claim(claim_id)
    node_id = agent.ensure_enrolled(max_polls=2)  # resumes the persisted claim
    assert node_id.startswith("node_")


def test_desired_fetch_and_observation_across_repos(live, tmp_path):
    app, url = live
    svc = app.state.control_service
    token = svc.create_token(auto_approve=True)
    agent, transport = _agent(tmp_path, url, token)
    node_id = agent.ensure_enrolled()
    agent.establish_session()

    svc.set_desired(node_id, {"node_id": node_id, "generation": 2,
                              "snapshot_hash": "sha256:" + "0" * 64, "assignments": []})
    resp = agent.heartbeat_once()
    assert resp["desired_generation"] == 2
    assert transport.fetch_desired_state(node_id, 2)["generation"] == 2

    transport.post_observation(node_id, {"node_id": node_id, "agent_session_id": agent.session_id,
                                         "sequence": 1, "observed_generation": 2, "engines": []})
    from meridian_control.models import ObservationRow
    with svc._sf() as s:
        assert s.get(ObservationRow, node_id).observation["observed_generation"] == 2
