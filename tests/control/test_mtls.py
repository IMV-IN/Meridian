"""mTLS identity binding (DESIGN.md 15.6): post-enrollment calls must carry a
CA-issued node cert whose SAN node_id matches the path. Covers the server-side
`verify_node_identity` logic and an end-to-end pass/reject through the real app.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.parse

import pytest
from conftest import MutableClock
from cryptography.hazmat.primitives import serialization

from meridian_control.app import create_app
from meridian_control.ca import NodeCA
from meridian_control.config import ControlConfig
from meridian_control.db import make_session_factory
from meridian_control.service import ControlService, ControlServiceError


def _mtls_service(tmp_path, require_mtls=True):
    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp_path}/control.db", ca_dir=tmp_path / "ca",
        lease_ttl_seconds=30, require_mtls=require_mtls,
    )
    ca = NodeCA.load_or_create(cfg.ca_dir)
    return ControlService(make_session_factory(cfg.db_url), ca, cfg, now=MutableClock()), ca


def _enroll(svc, node_key):
    token = svc.create_token(auto_approve=True)
    resp = svc.enroll(token, {"node_public_key": node_key.public_b64url()})
    return resp["node_id"], resp["certificate"]


def test_matching_cert_is_accepted(tmp_path, node_key):
    svc, _ = _mtls_service(tmp_path)
    node_id, cert = _enroll(svc, node_key)
    svc.verify_node_identity(node_id, urllib.parse.quote(cert))  # no raise


def test_missing_cert_rejected_when_required(tmp_path, node_key):
    svc, _ = _mtls_service(tmp_path)
    node_id, _ = _enroll(svc, node_key)
    with pytest.raises(ControlServiceError) as exc:
        svc.verify_node_identity(node_id, None)
    assert exc.value.code == "NODE_NOT_AUTHORIZED"


def test_cert_for_another_node_rejected(tmp_path, node_key):
    svc, _ = _mtls_service(tmp_path)
    node_a, cert_a = _enroll(svc, node_key)
    node_b, _ = _enroll(svc, node_key)
    assert node_a != node_b
    with pytest.raises(ControlServiceError) as exc:
        svc.verify_node_identity(node_b, urllib.parse.quote(cert_a))
    assert exc.value.code == "NODE_NOT_AUTHORIZED"


def test_cert_from_foreign_ca_rejected(tmp_path, node_key):
    svc, _ = _mtls_service(tmp_path)
    node_id, _ = _enroll(svc, node_key)
    foreign = NodeCA.load_or_create(tmp_path / "foreign-ca")
    raw_pub = node_key.key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    forged = foreign.issue_node_cert(node_id, raw_pub)
    with pytest.raises(ControlServiceError) as exc:
        svc.verify_node_identity(node_id, urllib.parse.quote(forged))
    assert exc.value.code == "NODE_NOT_AUTHORIZED"


def test_disabled_is_noop(tmp_path, node_key):
    svc, _ = _mtls_service(tmp_path, require_mtls=False)
    node_id, _ = _enroll(svc, node_key)
    svc.verify_node_identity(node_id, None)  # no raise even with no cert


# --- end-to-end through the real app ------------------------------------
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_mtls(tmp_path):
    import uvicorn

    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp_path}/x.db", ca_dir=tmp_path / "ca",
        lease_ttl_seconds=30, require_mtls=True,
    )
    app = create_app(cfg)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
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


def test_agent_presents_cert_end_to_end(live_mtls, tmp_path):
    pytest.importorskip("meridian_node")
    from meridian_node.agent import Agent
    from meridian_node.config import Config
    from meridian_node.control import ControlError
    from meridian_node.http_transport import HttpTransport
    from meridian_node.wiring import upgrade_transport

    app, url = live_mtls
    token = app.state.control_service.create_token(auto_approve=True)
    cfg = Config(control_plane_url=url, state_dir=tmp_path / "node", mode="observe-only")
    agent = Agent(cfg, HttpTransport(url, enrollment_token=token))
    node_id = agent.ensure_enrolled()

    # Without the forwarded cert header the session is rejected.
    with pytest.raises(ControlError) as exc:
        agent.establish_session()
    assert exc.value.code == "NODE_NOT_AUTHORIZED"

    # After upgrading to the cert transport, the same call is authorized.
    upgrade_transport(cfg, agent)
    agent.establish_session()
    assert agent.heartbeat_once()["accepted_sequence"] == 1
    assert node_id.startswith("node_")
