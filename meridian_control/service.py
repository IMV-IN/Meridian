"""Control-plane service logic (DESIGN.md 8.1, 10, 15, 17.5).

Durable version of the node repo's reference core: single-use hashed tokens,
approval gating, real Ed25519 certificate issuance, epoch-fenced sessions with a
restore-safe epoch floor, sequence-checked heartbeats, desired-state
generations, and observations. All state lives in SQLAlchemy; nothing is held in
process memory across requests, so control replicas are stateless.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from .ca import NodeCA, node_id_from_cert
from .config import ControlConfig
from .models import (
    AuditEvent,
    Claim,
    DesiredSnapshotRow,
    EnrollmentToken,
    Incarnation,
    Node,
    ObservationRow,
    StopAuthorization,
)


class ControlServiceError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, http_status: int = 400):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ControlService:
    def __init__(self, session_factory, ca: NodeCA, config: ControlConfig, now: Callable[[], datetime] = _utcnow):
        self._sf = session_factory
        self._ca = ca
        self._config = config
        self._now = now

    def _ttl(self) -> timedelta:
        return timedelta(seconds=self._config.lease_ttl_seconds)

    def _incarnation(self, session) -> Incarnation:
        inc = session.get(Incarnation, 1)
        if inc is None:
            inc = Incarnation(id=1, value=1, epoch_floor=0)
            session.add(inc)
            session.flush()
        return cast(Incarnation, inc)

    # --- admin ----------------------------------------------------------
    def create_token(self, auto_approve: bool = False, ttl_seconds: int = 3600) -> str:
        token = "enr_" + secrets.token_urlsafe(24)
        with self._sf() as s:
            s.add(EnrollmentToken(
                token_hash=_hash_token(token), auto_approve=auto_approve,
                expires_at=self._now() + timedelta(seconds=ttl_seconds),
            ))
            s.commit()
        return token

    def approve_claim(self, claim_id: str) -> None:
        self._set_claim_status(claim_id, "approved")

    def deny_claim(self, claim_id: str) -> None:
        self._set_claim_status(claim_id, "denied")

    def _set_claim_status(self, claim_id: str, status: str) -> None:
        with self._sf() as s:
            claim = s.get(Claim, claim_id)
            if claim is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown claim", http_status=404)
            claim.status = status
            s.commit()

    def set_desired(self, node_id: str, snapshot: dict) -> None:
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            generation = int(snapshot["generation"])
            existing = s.scalar(
                select(DesiredSnapshotRow).where(
                    DesiredSnapshotRow.node_id == node_id, DesiredSnapshotRow.generation == generation
                )
            )
            if existing is None:
                s.add(DesiredSnapshotRow(node_id=node_id, generation=generation, snapshot=snapshot))
            else:
                existing.snapshot = snapshot
            if generation > node.desired_generation:
                node.desired_generation = generation
            s.commit()

    def authorize_stop(self, node_id: str, engine_ids: list[str]) -> None:
        with self._sf() as s:
            s.query(StopAuthorization).filter(StopAuthorization.node_id == node_id).delete()
            for eid in engine_ids:
                s.add(StopAuthorization(node_id=node_id, engine_id=eid))
            s.commit()

    def revoke_node(self, node_id: str) -> None:
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            node.revoked = True
            s.add(AuditEvent(node_id=node_id, kind="node_revoked"))
            s.commit()

    def record_restore(self, high_water_epoch: int) -> int:
        """Restore-from-backup runbook step (DESIGN.md 17.5). Increments the
        control-plane incarnation and raises the epoch floor past the last-issued
        high-water mark so a regressed epoch can never re-admit a fenced agent."""
        with self._sf() as s:
            inc = self._incarnation(s)
            inc.value += 1
            inc.epoch_floor = max(inc.epoch_floor, high_water_epoch)
            s.add(AuditEvent(kind="control_restore", detail=f"incarnation={inc.value} floor={inc.epoch_floor}"))
            s.commit()
            return inc.value

    # --- enrollment -----------------------------------------------------
    def enroll(self, token: Optional[str], request: dict) -> dict:
        with self._sf() as s:
            row = s.get(EnrollmentToken, _hash_token(token)) if token else None
            if row is None:
                raise ControlServiceError("INVALID_CREDENTIAL", "unknown enrollment token", http_status=401)
            if row.used:
                raise ControlServiceError("INVALID_CREDENTIAL", "enrollment token already used", http_status=401)
            if row.expires_at is not None and self._now() > _aware(row.expires_at):
                raise ControlServiceError("INVALID_CREDENTIAL", "enrollment token expired", http_status=401)
            row.used = True

            node_id = "node_" + secrets.token_hex(12)
            public_key = _b64url_decode(request["node_public_key"])
            if row.auto_approve:
                self._create_node(s, node_id, public_key, request)
                resp = self._approved_response(s, node_id)
                s.add(AuditEvent(node_id=node_id, kind="enrolled_auto"))
                s.commit()
                return resp

            claim_id = "claim_" + secrets.token_hex(12)
            s.add(Claim(claim_id=claim_id, node_id=node_id, public_key=public_key, nonce=secrets.token_hex(32)))
            s.add(AuditEvent(node_id=node_id, kind="claim_created", detail=claim_id))
            s.commit()
            return {"status": "pending", "claim_id": claim_id}

    def claim_nonce(self, claim_id: str) -> str:
        with self._sf() as s:
            claim = s.get(Claim, claim_id)
            if claim is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown claim", http_status=404)
            return str(claim.nonce)

    def resolve_claim(self, claim_id: str, signature: bytes) -> dict:
        with self._sf() as s:
            claim = s.get(Claim, claim_id)
            if claim is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown claim", http_status=404)
            try:
                Ed25519PublicKey.from_public_bytes(claim.public_key).verify(signature, claim.nonce.encode())
            except InvalidSignature as e:
                raise ControlServiceError("INVALID_CREDENTIAL", "claim possession proof failed", http_status=401) from e
            if claim.status == "denied":
                return {"status": "denied"}
            if claim.status != "approved":
                return {"status": "pending", "claim_id": claim_id}
            if s.get(Node, claim.node_id) is None:
                self._create_node(s, claim.node_id, claim.public_key, {})
            resp = self._approved_response(s, claim.node_id)
            s.add(AuditEvent(node_id=claim.node_id, kind="enrolled_approved"))
            s.commit()
            return resp

    def _create_node(self, s, node_id: str, public_key: bytes, request: dict) -> None:
        cert = self._ca.issue_node_cert(node_id, public_key, self._config.cert_lifetime_hours)
        host_claims = request.get("host_claims", {}) if request else {}
        s.add(Node(
            node_id=node_id, public_key=public_key, certificate_pem=cert,
            display_name=host_claims.get("hostname", ""),
        ))
        s.flush()

    def _approved_response(self, s, node_id: str) -> dict:
        node = s.get(Node, node_id)
        return {
            "status": "approved",
            "node_id": node_id,
            "certificate": node.certificate_pem,
            "trust_bundle": self._ca.trust_bundle(),
            "protocol_version": "v1",
            "lease": {
                "heartbeat_interval_seconds": self._config.heartbeat_interval_seconds,
                "lease_ttl_seconds": self._config.lease_ttl_seconds,
            },
        }

    # --- transport auth -------------------------------------------------
    def verify_node_identity(self, node_id: str, cert_header: Optional[str]) -> None:
        """Bind the presented node certificate to the path node_id (DESIGN.md 15.6).
        No-op unless `require_mtls`. The edge terminates mTLS and forwards the
        url-escaped PEM; here we re-verify the chain and match the SAN node_id."""
        if not self._config.require_mtls:
            return
        if not cert_header:
            raise ControlServiceError(
                "NODE_NOT_AUTHORIZED", "client certificate required", http_status=401
            )
        try:
            cert = self._ca.verify_cert(urllib.parse.unquote(cert_header))
            cert_node_id = node_id_from_cert(cert)
        except ValueError as e:
            raise ControlServiceError("NODE_NOT_AUTHORIZED", str(e), http_status=403) from e
        if cert_node_id != node_id:
            raise ControlServiceError(
                "NODE_NOT_AUTHORIZED", "certificate identity does not match node", http_status=403
            )

    def rotate_certificate(self, node_id: str) -> dict:
        """Re-issue a node cert for the node's registered public key (Phase 4).
        Gated by the current cert (verify_node_identity) at the route. Safe to
        expose: the new cert is bound to the on-record public key, so only the
        holder of that private key can use it."""
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            if node.revoked:
                raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
            cert = self._ca.issue_node_cert(node_id, node.public_key, self._config.cert_lifetime_hours)
            node.certificate_pem = cert
            s.add(AuditEvent(node_id=node_id, kind="cert_rotated"))
            s.commit()
            return {
                "certificate": cert,
                "trust_bundle": self._ca.trust_bundle(),
                "cert_lifetime_hours": self._config.cert_lifetime_hours,
            }

    # --- session --------------------------------------------------------
    def establish_session(self, node_id: str, request: dict) -> dict:
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            if node.revoked:
                raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
            inc = self._incarnation(s)
            session_id = request["agent_session_id"]
            lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
            has_active = node.active_session_id is not None and node.active_session_id != session_id
            if lease_valid and has_active and not request.get("handoff_token"):
                raise ControlServiceError(
                    "CONFLICT", "active session holds a valid lease; wait for expiry or supply a handoff token",
                    http_status=409,
                )
            new_epoch = max(node.fencing_epoch, inc.epoch_floor) + 1
            node.fencing_epoch = new_epoch
            node.incarnation = inc.value
            node.active_session_id = session_id
            node.lease_expires_at = self._now() + self._ttl()
            s.add(AuditEvent(node_id=node_id, kind="session_established", detail=f"epoch={new_epoch}"))
            s.commit()
            return {
                "fencing_epoch": new_epoch,
                "lease_expires_at": _iso(node.lease_expires_at),
                "session_assertion": self._session_assertion(node_id, session_id, new_epoch, inc.value),
            }

    def _session_assertion(self, node_id: str, session_id: str, epoch: int, incarnation: int) -> str:
        # A server-issued binding of identity+epoch+incarnation. Opaque to the
        # agent today; the helper's epoch record is the enforced boundary.
        payload = {"node_id": node_id, "session_id": session_id, "epoch": epoch, "incarnation": incarnation}
        return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")

    # --- heartbeat ------------------------------------------------------
    def heartbeat(self, node_id: str, request: dict) -> dict:
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            if node.revoked:
                raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
            if request["agent_session_id"] != node.active_session_id or request["fencing_epoch"] != node.fencing_epoch:
                raise ControlServiceError("STALE_AGENT_SESSION", "agent session is no longer active", http_status=409)
            if request["sequence"] <= node.highest_sequence:
                raise ControlServiceError("CONFLICT", "sequence is not greater than highest accepted", http_status=409)
            node.highest_sequence = request["sequence"]
            node.lease_expires_at = self._now() + self._ttl()
            stop_ids = [
                r.engine_id for r in s.scalars(
                    select(StopAuthorization).where(StopAuthorization.node_id == node_id)
                )
            ]
            s.commit()
            return {
                "server_time": _iso(self._now()),
                "accepted_sequence": request["sequence"],
                "fencing_epoch": node.fencing_epoch,
                "lease_expires_at": _iso(node.lease_expires_at),
                "desired_generation": node.desired_generation,
                "admission_projection_revision": 0,
                "stop_authorized_engine_ids": sorted(stop_ids),
            }

    # --- desired state / observations ----------------------------------
    def fetch_desired_state(self, node_id: str, generation: int) -> dict:
        with self._sf() as s:
            if s.get(Node, node_id) is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            row = s.scalar(
                select(DesiredSnapshotRow).where(DesiredSnapshotRow.node_id == node_id)
                .order_by(DesiredSnapshotRow.generation.desc())
            )
            if row is None:
                raise ControlServiceError("GENERATION_NOT_FOUND", "no desired state published", http_status=404)
            return dict(row.snapshot)

    def post_observation(self, node_id: str, observation: dict) -> dict:
        with self._sf() as s:
            if s.get(Node, node_id) is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            seq = int(observation.get("sequence", 0))
            row = s.get(ObservationRow, node_id)
            if row is None:
                s.add(ObservationRow(node_id=node_id, sequence=seq, observation=observation))
            elif seq >= row.sequence:
                row.sequence = seq
                row.observation = observation
            s.commit()
            return {"received": True}

    # --- placement (Phase 5, control side) -----------------------------
    def select_placement(self, required_vram_bytes: int) -> Optional[dict]:
        """Pick a routable node + device with enough reported allocatable VRAM
        (DESIGN.md 24 P3). Consumes the capacity nodes report in observations;
        prefers the device with the most headroom (spread). None if nothing fits."""
        best: Optional[tuple[int, str, str]] = None  # (allocatable, node_id, device_id)
        with self._sf() as s:
            for node in s.scalars(select(Node)):
                lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
                if node.revoked or not lease_valid:
                    continue
                obs = s.get(ObservationRow, node.node_id)
                if obs is None:
                    continue
                allocatable = obs.observation.get("capacity", {}).get("allocatable_vram_bytes", {})
                for device_id, free in allocatable.items():
                    free_i = int(free)
                    if free_i >= required_vram_bytes and (best is None or free_i > best[0]):
                        best = (free_i, node.node_id, device_id)
        if best is None:
            return None
        return {"node_id": best[1], "device_id": best[2], "allocatable_vram_bytes": best[0]}

    # --- read models (operator / gateway projection) -------------------
    def serving_projection(self) -> list[dict]:
        """Read-only projection the gateway consumes (DESIGN.md 17). Routable
        endpoints are those with a valid lease and a Ready engine observation."""
        out: list[dict] = []
        with self._sf() as s:
            for node in s.scalars(select(Node)):
                lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
                obs = s.get(ObservationRow, node.node_id)
                engines = (obs.observation.get("engines", []) if obs else [])
                for e in engines:
                    if e.get("phase") == "Ready" and e.get("endpoint"):
                        out.append({
                            "engine_id": e["engine_id"], "node_id": node.node_id,
                            "endpoint": e["endpoint"], "model": e.get("model", ""),
                            "provider": "managed",
                            "routable": bool(lease_valid) and not node.revoked,
                        })
        return out


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)
