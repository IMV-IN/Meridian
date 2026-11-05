"""Operator console read models: overview, nodes, node detail, claims, tokens,
audit, and opt-in CORS. They must reflect the durable state the write paths
produce and never expose private material (public keys, nonces, raw tokens)."""

from __future__ import annotations

import pytest
from conftest import MutableClock, NodeKey
from fastapi.testclient import TestClient

from meridian_control.app import create_app
from meridian_control.config import ControlConfig


@pytest.fixture
def clock():
    return MutableClock()


def _app(tmp_path, clock, **overrides):
    cfg = ControlConfig(
        db_url=f"sqlite:///{tmp_path}/console.db", ca_dir=tmp_path / "ca", lease_ttl_seconds=30, **overrides,
    )
    return TestClient(create_app(cfg, now=clock))


@pytest.fixture
def client(tmp_path, clock):
    return _app(tmp_path, clock)


def _enroll(client, key: NodeKey, hostname: str = "gpu-a") -> str:
    token = client.post("/admin/tokens", json={"auto_approve": True}).json()["token"]
    r = client.post(
        "/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
        json={"node_public_key": key.public_b64url(), "host_claims": {"hostname": hostname}},
    )
    assert r.json()["status"] == "approved"
    return r.json()["node_id"]


def _session(client, node_id: str) -> int:
    return client.post(f"/control/v1/nodes/{node_id}/sessions", json={"agent_session_id": "s1"}).json()["fencing_epoch"]


def _observe(client, node_id: str, sequence: int = 1, phase: str = "Ready") -> None:
    client.post(f"/control/v1/nodes/{node_id}/observations", json={
        "node_id": node_id, "agent_session_id": "s1", "sequence": sequence, "observed_generation": 3,
        "engines": [
            {"engine_id": "e1", "phase": phase, "endpoint": "https://10.0.0.5:8000", "model": "qwen", "queue_depth": 2},
            {"engine_id": "e2", "phase": "Failed"},
        ],
        "capacity": {
            "allocatable_vram_bytes": {"GPU-0": 8 * 2**30, "GPU-1": 4 * 2**30},
            "free_ports": 12, "held_artifacts": ["sha256:aa"], "running_engines": 1, "queue_depth": 2,
            "nvlink_groups": {},
        },
    })


def test_nodes_list_reflects_lease_and_observation(client, clock):
    node_id = _enroll(client, NodeKey())
    nodes = client.get("/admin/nodes").json()["nodes"]
    assert len(nodes) == 1 and nodes[0]["status"] == "enrolled"
    assert nodes[0]["display_name"] == "gpu-a"
    assert nodes[0]["capacity"] is None

    _session(client, node_id)
    _observe(client, node_id)
    n = client.get("/admin/nodes").json()["nodes"][0]
    assert n["status"] == "online"
    assert n["observed_generation"] == 3
    assert n["engines"] == {"total": 2, "ready": 1, "failed": 1, "by_phase": {"Ready": 1, "Failed": 1}}
    assert n["capacity"]["devices"] == 2
    assert n["capacity"]["allocatable_vram_bytes"] == 12 * 2**30
    assert n["observed_at"] == "2026-08-01T12:00:00Z"

    clock.advance(31)
    assert client.get("/admin/nodes").json()["nodes"][0]["status"] == "offline"

    client.post(f"/admin/nodes/{node_id}/revoke")
    assert client.get("/admin/nodes").json()["nodes"][0]["status"] == "revoked"


def test_node_detail_includes_desired_cert_stops_and_audit(client):
    node_id = _enroll(client, NodeKey())
    _session(client, node_id)
    _observe(client, node_id)
    client.post(f"/admin/nodes/{node_id}/desired", json={"node_id": node_id, "generation": 4, "assignments": []})
    client.post(f"/admin/nodes/{node_id}/stop-authorize", json={"engine_ids": ["e2", "e1"]})

    d = client.get(f"/admin/nodes/{node_id}").json()
    assert d["desired_generation"] == 4
    assert d["desired"]["generation"] == 4
    assert d["desired"]["snapshot"]["assignments"] == []
    assert d["stop_authorized_engine_ids"] == ["e1", "e2"]
    assert d["observation"]["engines"][0]["engine_id"] == "e1"
    assert d["certificate"]["serial"].isdigit()
    assert d["certificate"]["not_valid_after"].endswith("Z")
    kinds = [e["kind"] for e in d["audit"]]
    assert kinds[0] == "session_established" and "enrolled_auto" in kinds
    # Private material is never part of the read model.
    assert "public_key" not in d and "certificate_pem" not in d


def test_node_detail_unknown_is_404(client):
    r = client.get("/admin/nodes/node_missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NODE_NOT_FOUND"


def test_claims_and_tokens_views(client):
    key = NodeKey()
    token = client.post("/admin/tokens", json={"auto_approve": False}).json()["token"]
    client.post("/admin/tokens", json={"auto_approve": True})
    claim_id = client.post(
        "/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
        json={"node_public_key": key.public_b64url()},
    ).json()["claim_id"]

    pending = client.get("/admin/claims", params={"status": "pending"}).json()["claims"]
    assert [c["claim_id"] for c in pending] == [claim_id]
    assert set(pending[0]) == {"claim_id", "node_id", "status", "created_at"}

    client.post(f"/admin/claims/{claim_id}/approve")
    assert client.get("/admin/claims", params={"status": "pending"}).json()["claims"] == []
    assert client.get("/admin/claims").json()["claims"][0]["status"] == "approved"

    tokens = client.get("/admin/tokens").json()["tokens"]
    assert sorted(t["status"] for t in tokens) == ["active", "used"]
    assert all(len(t["token_ref"]) == 12 for t in tokens)
    assert token not in str(tokens)


def test_tokens_expire(client, clock):
    client.post("/admin/tokens", json={"ttl_seconds": 60})
    clock.advance(61)
    assert client.get("/admin/tokens").json()["tokens"][0]["status"] == "expired"


def test_audit_view_filters_and_limits(client):
    a = _enroll(client, NodeKey(), "a")
    b = _enroll(client, NodeKey(), "b")
    _session(client, a)
    events = client.get("/admin/audit").json()["events"]
    assert events[0]["kind"] == "session_established"
    assert {e["node_id"] for e in events} == {a, b}
    only_b = client.get("/admin/audit", params={"node_id": b}).json()["events"]
    assert {e["node_id"] for e in only_b} == {b}
    assert len(client.get("/admin/audit", params={"limit": 1}).json()["events"]) == 1


def test_overview_counts(client):
    a = _enroll(client, NodeKey(), "a")
    _enroll(client, NodeKey(), "b")
    _session(client, a)
    _observe(client, a)
    client.post("/admin/tokens", json={"auto_approve": False})
    ov = client.get("/admin/overview").json()
    assert ov["nodes"] == {"total": 2, "by_status": {"online": 1, "enrolled": 1}}
    assert ov["engines"] == {"total": 2, "ready": 1, "failed": 1}
    assert ov["routable_endpoints"] == 1
    assert ov["allocatable_vram_bytes"] == 12 * 2**30
    assert ov["incarnation"] == 1
    assert ov["settings"]["lease_ttl_seconds"] == 30


def test_cors_off_by_default(client):
    r = client.get("/admin/overview", headers={"Origin": "http://console.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_opt_in(tmp_path, clock):
    c = _app(tmp_path, clock, cors_origins=("http://console.example",))
    ok = c.get("/admin/overview", headers={"Origin": "http://console.example"})
    assert ok.headers["access-control-allow-origin"] == "http://console.example"
    other = c.get("/admin/overview", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_env_parsing(monkeypatch):
    monkeypatch.setenv("MERIDIAN_CONTROL_CORS_ORIGINS", " http://a:8080 , ,http://b ")
    assert ControlConfig.from_env().cors_origins == ("http://a:8080", "http://b")
