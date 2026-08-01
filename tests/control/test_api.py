"""HTTP surface via FastAPI TestClient: the full enroll/session/heartbeat flow,
the pending-approval possession handshake, and the error envelope."""

from __future__ import annotations

import pytest
from conftest import NodeKey
from fastapi.testclient import TestClient

from meridian_control.app import create_app
from meridian_control.config import ControlConfig


@pytest.fixture
def client(tmp_path):
    cfg = ControlConfig(db_url=f"sqlite:///{tmp_path}/api.db", ca_dir=tmp_path / "ca", lease_ttl_seconds=30)
    return TestClient(create_app(cfg))


def _mint(client, auto_approve=True):
    return client.post("/admin/tokens", json={"auto_approve": auto_approve}).json()["token"]


def test_enroll_session_heartbeat_over_http(client):
    key = NodeKey()
    token = _mint(client, auto_approve=True)
    r = client.post("/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
                    json={"node_public_key": key.public_b64url()})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    node_id = r.json()["node_id"]

    s = client.post(f"/control/v1/nodes/{node_id}/sessions", json={"agent_session_id": "s1"})
    epoch = s.json()["fencing_epoch"]
    h = client.post(f"/control/v1/nodes/{node_id}/heartbeat",
                    json={"agent_session_id": "s1", "fencing_epoch": epoch, "sequence": 1})
    assert h.status_code == 200 and h.json()["accepted_sequence"] == 1


def test_pending_approval_handshake_over_http(client):
    key = NodeKey()
    token = _mint(client, auto_approve=False)
    r = client.post("/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
                    json={"node_public_key": key.public_b64url()})
    claim_id = r.json()["claim_id"]

    # Step 1: fetch the nonce. Step 2: prove possession.
    nonce = client.get(f"/control/v1/enroll/claims/{claim_id}").json()["nonce"]
    proof = key.sign_b64url(nonce.encode())
    pending = client.get(f"/control/v1/enroll/claims/{claim_id}", headers={"X-Possession-Proof": proof})
    assert pending.json()["status"] == "pending"

    client.post(f"/admin/claims/{claim_id}/approve")
    approved = client.get(f"/control/v1/enroll/claims/{claim_id}", headers={"X-Possession-Proof": proof})
    assert approved.json()["status"] == "approved"


def test_stale_session_error_envelope(client):
    key = NodeKey()
    token = _mint(client, auto_approve=True)
    node_id = client.post("/control/v1/enroll", headers={"Authorization": f"Bearer {token}"},
                          json={"node_public_key": key.public_b64url()}).json()["node_id"]
    client.post(f"/control/v1/nodes/{node_id}/sessions", json={"agent_session_id": "s1"})
    # Wrong epoch -> 409 with a typed error envelope.
    r = client.post(f"/control/v1/nodes/{node_id}/heartbeat",
                    json={"agent_session_id": "s1", "fencing_epoch": 999, "sequence": 1})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "STALE_AGENT_SESSION"
    assert r.json()["error"]["retryable"] is False


def test_invalid_token_rejected(client):
    key = NodeKey()
    r = client.post("/control/v1/enroll", headers={"Authorization": "Bearer nope"},
                    json={"node_public_key": key.public_b64url()})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIAL"
