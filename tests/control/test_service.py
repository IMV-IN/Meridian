"""Durable control-service logic: enrollment, fencing, leases, sequence
monotonicity, restore-safe fencing, revocation, desired state, projection."""

from __future__ import annotations

import base64

import pytest
from conftest import make_service

from meridian_control.service import ControlServiceError


def _sig_bytes(node_key, nonce: str) -> bytes:
    return base64.urlsafe_b64decode(node_key.sign_b64url(nonce.encode()) + "==")


def _enroll_auto(svc, node_key):
    token = svc.create_token(auto_approve=True)
    return svc.enroll(token, {"node_public_key": node_key.public_b64url(), "host_claims": {"hostname": "h"}})


def test_enroll_auto_issues_cert_and_token_is_single_use(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    token = svc.create_token(auto_approve=True)
    resp = svc.enroll(token, {"node_public_key": node_key.public_b64url()})
    assert resp["status"] == "approved"
    assert "BEGIN CERTIFICATE" in resp["certificate"]
    with pytest.raises(ControlServiceError) as exc:
        svc.enroll(token, {"node_public_key": node_key.public_b64url()})
    assert exc.value.code == "INVALID_CREDENTIAL"


def test_pending_approval_flow(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    token = svc.create_token(auto_approve=False)
    resp = svc.enroll(token, {"node_public_key": node_key.public_b64url()})
    assert resp["status"] == "pending"
    claim_id = resp["claim_id"]

    nonce = svc.claim_nonce(claim_id)
    # Not approved yet -> still pending.
    assert svc.resolve_claim(claim_id, _sig_bytes(node_key, nonce))["status"] == "pending"

    svc.approve_claim(claim_id)
    approved = svc.resolve_claim(claim_id, _sig_bytes(node_key, nonce))
    assert approved["status"] == "approved"
    assert approved["node_id"].startswith("node_")


def test_resolve_claim_rejects_bad_signature(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    token = svc.create_token(auto_approve=False)
    claim_id = svc.enroll(token, {"node_public_key": node_key.public_b64url()})["claim_id"]
    svc.approve_claim(claim_id)
    with pytest.raises(ControlServiceError) as exc:
        svc.resolve_claim(claim_id, b"\x00" * 64)
    assert exc.value.code == "INVALID_CREDENTIAL"


def test_session_and_heartbeat_sequence(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    sess = svc.establish_session(node_id, {"agent_session_id": "s1"})
    epoch = sess["fencing_epoch"]
    assert epoch == 1

    hb = svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": epoch, "sequence": 5})
    assert hb["accepted_sequence"] == 5
    with pytest.raises(ControlServiceError) as exc:
        svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": epoch, "sequence": 5})
    assert exc.value.code == "CONFLICT"


def test_stale_session_rejected_after_takeover(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s1"})
    svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 1})

    clock.advance(31)  # lease expires
    svc.establish_session(node_id, {"agent_session_id": "s2"})  # epoch 2
    with pytest.raises(ControlServiceError) as exc:
        svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 2})
    assert exc.value.code == "STALE_AGENT_SESSION"


def test_session_blocked_while_lease_valid(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s1"})
    with pytest.raises(ControlServiceError) as exc:
        svc.establish_session(node_id, {"agent_session_id": "s2"})
    assert exc.value.code == "CONFLICT"
    clock.advance(31)
    assert svc.establish_session(node_id, {"agent_session_id": "s2"})["fencing_epoch"] == 2


def test_restore_raises_epoch_floor_and_fences_old_sessions(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s1"})  # epoch 1

    # Simulate restore from backup: ops record the last-issued high-water epoch.
    incarnation = svc.record_restore(high_water_epoch=9)
    assert incarnation == 2

    clock.advance(31)
    sess = svc.establish_session(node_id, {"agent_session_id": "s2"})
    assert sess["fencing_epoch"] == 10  # max(1, floor 9) + 1; never regresses

    # The pre-restore session can never re-admit itself.
    with pytest.raises(ControlServiceError) as exc:
        svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 1})
    assert exc.value.code == "STALE_AGENT_SESSION"


def test_revoked_node_cannot_heartbeat(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s1"})
    svc.revoke_node(node_id)
    with pytest.raises(ControlServiceError) as exc:
        svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 1})
    assert exc.value.code == "NODE_NOT_AUTHORIZED"


def test_desired_state_generation_and_fetch(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.set_desired(node_id, {"node_id": node_id, "generation": 3, "snapshot_hash": "sha256:x", "assignments": []})
    svc.establish_session(node_id, {"agent_session_id": "s1"})
    hb = svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 1})
    assert hb["desired_generation"] == 3
    assert svc.fetch_desired_state(node_id, 3)["generation"] == 3


def test_serving_projection_reflects_ready_and_lease(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s1"})
    svc.heartbeat(node_id, {"agent_session_id": "s1", "fencing_epoch": 1, "sequence": 1})
    svc.post_observation(node_id, {"sequence": 1, "engines": [
        {"engine_id": "e1", "phase": "Ready", "endpoint": "https://10.0.0.5:8000"}
    ]})
    proj = svc.serving_projection()
    assert proj and proj[0]["engine_id"] == "e1" and proj[0]["routable"] is True

    clock.advance(31)  # lease expired -> not routable
    assert svc.serving_projection()[0]["routable"] is False
