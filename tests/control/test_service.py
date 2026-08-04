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


def test_revocation_enforced_on_desired_and_observation(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.set_desired(node_id, {"node_id": node_id, "generation": 1, "snapshot_hash": "sha256:x", "assignments": []})
    svc.revoke_node(node_id)
    for call in (lambda: svc.fetch_desired_state(node_id, 1),
                 lambda: svc.post_observation(node_id, {"sequence": 1, "engines": []})):
        with pytest.raises(ControlServiceError) as exc:
            call()
        assert exc.value.code == "NODE_NOT_AUTHORIZED"


def test_crl_lists_revoked_nodes_with_serial(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    assert svc.crl() == []
    svc.revoke_node(node_id)
    crl = svc.crl()
    assert len(crl) == 1 and crl[0]["node_id"] == node_id
    assert crl[0]["serial"] and crl[0]["serial"].isdigit()


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


def test_rotate_certificate_reissues_for_same_key(tmp_path, clock, node_key):
    from cryptography import x509

    svc = make_service(tmp_path, clock)
    resp = _enroll_auto(svc, node_key)
    node_id, old_cert = resp["node_id"], resp["certificate"]

    rotated = svc.rotate_certificate(node_id)
    assert rotated["certificate"] != old_cert
    cert = x509.load_pem_x509_certificate(rotated["certificate"].encode())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert f"meridian-node://{node_id}" in san.get_values_for_type(x509.UniformResourceIdentifier)
    # the reissued cert still verifies against the same CA
    svc._ca.verify_cert(rotated["certificate"])


def _ready_node(svc, node_key, capacity):
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.establish_session(node_id, {"agent_session_id": "s-" + node_id})
    svc.heartbeat(node_id, {"agent_session_id": "s-" + node_id, "fencing_epoch": 1, "sequence": 1})
    svc.post_observation(node_id, {"sequence": 1, "engines": [], "capacity": capacity})
    return node_id


def _ready_node_with_capacity(svc, node_key, allocatable):
    return _ready_node(svc, node_key, {"allocatable_vram_bytes": allocatable})


def test_select_placement_prefers_most_headroom(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    _ready_node_with_capacity(svc, node_key, {"GPU-0": 8 * g})
    big = _ready_node_with_capacity(svc, node_key, {"GPU-0": 40 * g, "GPU-1": 20 * g})

    pick = svc.select_placement(10 * g)
    assert pick is not None
    assert pick["node_id"] == big and pick["device_id"] == "GPU-0"  # most headroom that fits
    assert pick["allocatable_vram_bytes"] == 40 * g

    assert svc.select_placement(100 * g) is None  # nothing fits


def test_select_placement_skips_expired_lease(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    _ready_node_with_capacity(svc, node_key, {"GPU-0": 40 * g})
    clock.advance(31)  # lease (ttl 30) expired -> node not eligible
    assert svc.select_placement(10 * g) is None


def test_placement_prefers_artifact_locality(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    holder = _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 20 * g},
                                         "held_artifacts": ["sha256:aa"]})
    _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 40 * g}})  # more headroom, no artifact
    pick = svc.select_placement(10 * g, artifact_digest="sha256:aa")
    assert pick["node_id"] == holder  # locality beats headroom


def test_placement_load_tiebreak(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 40 * g}, "running_engines": 3})
    idle = _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 40 * g}, "running_engines": 0})
    assert svc.select_placement(10 * g)["node_id"] == idle  # equal headroom -> lower load


def test_placement_prefers_lower_queue_depth(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    # Busy node has fewer engines but a deeper queue; queue depth wins over count.
    _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 40 * g},
                                "running_engines": 1, "queue_depth": 12})
    quiet = _ready_node(svc, node_key, {"allocatable_vram_bytes": {"GPU-0": 40 * g},
                                        "running_engines": 4, "queue_depth": 2})
    assert svc.select_placement(10 * g)["node_id"] == quiet


def test_placement_multi_gpu_requires_nvlink_group(tmp_path, clock, node_key):
    g = 1024**3
    svc = make_service(tmp_path, clock)
    affine = _ready_node(svc, node_key, {
        "allocatable_vram_bytes": {"GPU-0": 10 * g, "GPU-1": 10 * g},
        "nvlink_groups": {"GPU-0": "nv0", "GPU-1": "nv0"}})
    _ready_node(svc, node_key, {  # two devices, but split across groups
        "allocatable_vram_bytes": {"GPU-0": 10 * g, "GPU-1": 10 * g},
        "nvlink_groups": {"GPU-0": "a", "GPU-1": "b"}})
    pick = svc.select_placement(5 * g, count=2)
    assert pick["node_id"] == affine
    assert len(pick["device_ids"]) == 2


def test_rotate_certificate_rejects_revoked_node(tmp_path, clock, node_key):
    svc = make_service(tmp_path, clock)
    node_id = _enroll_auto(svc, node_key)["node_id"]
    svc.revoke_node(node_id)
    with pytest.raises(ControlServiceError) as exc:
        svc.rotate_certificate(node_id)
    assert exc.value.code == "NODE_NOT_AUTHORIZED"
