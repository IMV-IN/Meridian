"""Control-plane service logic (DESIGN.md 8.1, 10, 15, 17.5).

Durable version of the node repo's reference core: single-use hashed tokens,
approval gating, real Ed25519 certificate issuance, epoch-fenced sessions with a
restore-safe epoch floor, sequence-checked heartbeats, desired-state
generations, and observations. All state lives in SQLAlchemy; nothing is held in
process memory across requests, so control replicas are stateless.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Optional, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from . import validation
from .ca import NodeCA, node_id_from_cert
from .config import ControlConfig
from .errors import ControlServiceError
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

if TYPE_CHECKING:
    from .compute import ComputeService

__all__ = ["ControlService", "ControlServiceError"]


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
        # Meridian Compute (cloud mode). When set, it owns the `jobs` list of
        # every desired snapshot and is told about each observation.
        self.compute: Optional["ComputeService"] = None

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
    # Every admin method takes an optional `workspace_id`. None = standalone
    # (unscoped legacy /admin API); a value scopes reads and rejects writes to
    # rows of another workspace with the same 404 as a missing row.
    def create_token(
        self, auto_approve: bool = False, ttl_seconds: int = 3600,
        workspace_id: Optional[str] = None, actor: str = "",
    ) -> str:
        token = "enr_" + secrets.token_urlsafe(24)
        with self._sf() as s:
            s.add(EnrollmentToken(
                token_hash=_hash_token(token), auto_approve=auto_approve,
                expires_at=self._now() + timedelta(seconds=ttl_seconds), workspace_id=workspace_id,
            ))
            if workspace_id:
                s.add(AuditEvent(
                    workspace_id=workspace_id, actor=actor, kind="enrollment_token_minted",
                    detail=f"auto_approve={auto_approve} ttl={ttl_seconds}s",
                ))
            s.commit()
        return token

    def approve_claim(self, claim_id: str, workspace_id: Optional[str] = None, actor: str = "") -> None:
        self._set_claim_status(claim_id, "approved", workspace_id, actor)

    def deny_claim(self, claim_id: str, workspace_id: Optional[str] = None, actor: str = "") -> None:
        self._set_claim_status(claim_id, "denied", workspace_id, actor)

    def _set_claim_status(
        self, claim_id: str, status: str, workspace_id: Optional[str] = None, actor: str = ""
    ) -> None:
        with self._sf() as s:
            claim = s.get(Claim, claim_id)
            if claim is None or (workspace_id is not None and claim.workspace_id != workspace_id):
                raise ControlServiceError("NODE_NOT_FOUND", "unknown claim", http_status=404)
            claim.status = status
            s.add(AuditEvent(
                node_id=claim.node_id, workspace_id=claim.workspace_id, actor=actor,
                kind=f"claim_{status}", detail=claim_id,
            ))
            s.commit()

    def _scoped_node(self, s, node_id: str, workspace_id: Optional[str]) -> Node:
        node = s.get(Node, node_id)
        if node is None or (workspace_id is not None and node.workspace_id != workspace_id):
            raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
        return cast(Node, node)

    def set_desired(self, node_id: str, snapshot: dict, workspace_id: Optional[str] = None, actor: str = "") -> None:
        with self._sf() as s:
            node = self._scoped_node(s, node_id, workspace_id)
            generation = int(snapshot["generation"])
            if self.compute is not None:
                if generation <= node.desired_generation:
                    raise ControlServiceError(
                        "LOWER_GENERATION",
                        f"this node is already at generation {node.desired_generation}; reload and publish again",
                        http_status=409,
                    )
                snapshot = self.compute.merge_owner_snapshot(s, node_id, snapshot)
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
            if actor or workspace_id:
                s.add(AuditEvent(
                    node_id=node_id, workspace_id=node.workspace_id, actor=actor,
                    kind="desired_published", detail=f"generation={generation}",
                ))
            s.commit()

    def authorize_stop(
        self, node_id: str, engine_ids: list[str], workspace_id: Optional[str] = None, actor: str = ""
    ) -> None:
        with self._sf() as s:
            if workspace_id is not None:
                self._scoped_node(s, node_id, workspace_id)
            s.query(StopAuthorization).filter(StopAuthorization.node_id == node_id).delete()
            for eid in engine_ids:
                s.add(StopAuthorization(node_id=node_id, engine_id=eid))
            if actor or workspace_id:
                s.add(AuditEvent(
                    node_id=node_id, workspace_id=workspace_id, actor=actor,
                    kind="stop_authorized", detail=",".join(engine_ids)[:1000],
                ))
            s.commit()

    def revoke_node(self, node_id: str, workspace_id: Optional[str] = None, actor: str = "") -> None:
        with self._sf() as s:
            node = self._scoped_node(s, node_id, workspace_id)
            node.revoked = True
            s.add(AuditEvent(node_id=node_id, workspace_id=node.workspace_id, actor=actor, kind="node_revoked"))
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
        req = validation.parse_report(validation.EnrollRequest, request)
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
            public_key = _b64url_decode(req.node_public_key)
            ws = row.workspace_id
            if row.auto_approve:
                self._create_node(s, node_id, public_key, req.host_claims.hostname, workspace_id=ws)
                resp = self._approved_response(s, node_id)
                s.add(AuditEvent(node_id=node_id, workspace_id=ws, kind="enrolled_auto"))
                s.commit()
                return resp

            claim_id = "claim_" + secrets.token_hex(12)
            s.add(Claim(
                claim_id=claim_id, node_id=node_id, public_key=public_key,
                nonce=secrets.token_hex(32), workspace_id=ws,
            ))
            s.add(AuditEvent(node_id=node_id, workspace_id=ws, kind="claim_created", detail=claim_id))
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
                self._create_node(s, claim.node_id, claim.public_key, "", workspace_id=claim.workspace_id)
            resp = self._approved_response(s, claim.node_id)
            s.add(AuditEvent(node_id=claim.node_id, workspace_id=claim.workspace_id, kind="enrolled_approved"))
            s.commit()
            return resp

    def _create_node(
        self, s, node_id: str, public_key: bytes, hostname: str, workspace_id: Optional[str] = None
    ) -> None:
        cert = self._ca.issue_node_cert(node_id, public_key, self._config.cert_lifetime_hours)
        s.add(Node(
            node_id=node_id, public_key=public_key, certificate_pem=cert,
            display_name=hostname, workspace_id=workspace_id,
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
        cert (url-escaped PEM, or base64 DER as Caddy's
        {http.request.tls.client.certificate_der_base64} emits it); here we
        re-verify the chain and match the SAN node_id."""
        if not self._config.require_mtls:
            return
        if not cert_header:
            raise ControlServiceError(
                "NODE_NOT_AUTHORIZED", "client certificate required", http_status=401
            )
        try:
            cert = self._ca.verify_cert(_cert_pem_from_header(cert_header))
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
            s.add(AuditEvent(node_id=node_id, workspace_id=node.workspace_id, kind="cert_rotated"))
            s.commit()
            return {
                "certificate": cert,
                "trust_bundle": self._ca.trust_bundle(),
                "cert_lifetime_hours": self._config.cert_lifetime_hours,
            }

    # --- session --------------------------------------------------------
    def establish_session(self, node_id: str, request: dict) -> dict:
        req = validation.parse_report(validation.NodeSessionRequest, request)
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            if node.revoked:
                raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
            inc = self._incarnation(s)
            session_id = req.agent_session_id
            lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
            has_active = node.active_session_id is not None and node.active_session_id != session_id
            if lease_valid and has_active and not req.handoff_token:
                raise ControlServiceError(
                    "CONFLICT", "active session holds a valid lease; wait for expiry or supply a handoff token",
                    http_status=409,
                )
            new_epoch = max(node.fencing_epoch, inc.epoch_floor) + 1
            node.fencing_epoch = new_epoch
            node.incarnation = inc.value
            node.active_session_id = session_id
            node.lease_expires_at = self._now() + self._ttl()
            s.add(AuditEvent(
                node_id=node_id, workspace_id=node.workspace_id,
                kind="session_established", detail=f"epoch={new_epoch}",
            ))
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
        hb = validation.parse_report(validation.NodeHeartbeat, request)
        with self._sf() as s:
            node = s.get(Node, node_id)
            if node is None:
                raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
            if node.revoked:
                raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
            if hb.agent_session_id != node.active_session_id or hb.fencing_epoch != node.fencing_epoch:
                raise ControlServiceError("STALE_AGENT_SESSION", "agent session is no longer active", http_status=409)
            if hb.sequence <= node.highest_sequence:
                raise ControlServiceError("CONFLICT", "sequence is not greater than highest accepted", http_status=409)
            node.highest_sequence = hb.sequence
            node.lease_expires_at = self._now() + self._ttl()
            if hb.gpus is not None:
                gpus = [validation.report_dict(g) for g in hb.gpus]
                if gpus != (node.inventory or []):
                    node.inventory = gpus
            stop_ids = [
                r.engine_id for r in s.scalars(
                    select(StopAuthorization).where(StopAuthorization.node_id == node_id)
                )
            ]
            s.commit()
            return {
                "server_time": _iso(self._now()),
                "accepted_sequence": hb.sequence,
                "fencing_epoch": node.fencing_epoch,
                "lease_expires_at": _iso(node.lease_expires_at),
                "desired_generation": node.desired_generation,
                "admission_projection_revision": 0,
                "stop_authorized_engine_ids": sorted(stop_ids),
            }

    def _active_node(self, s, node_id: str) -> Node:
        """Fetch a node, rejecting unknown and revoked ones. Revocation is thus
        enforced on every post-enrollment call, independent of the mTLS gate."""
        node = s.get(Node, node_id)
        if node is None:
            raise ControlServiceError("NODE_NOT_FOUND", "unknown node", http_status=404)
        if node.revoked:
            raise ControlServiceError("NODE_NOT_AUTHORIZED", "node certificate revoked", http_status=403)
        return cast(Node, node)

    # --- desired state / observations ----------------------------------
    def fetch_desired_state(self, node_id: str, generation: int) -> dict:
        with self._sf() as s:
            self._active_node(s, node_id)
            row = s.scalar(
                select(DesiredSnapshotRow).where(DesiredSnapshotRow.node_id == node_id)
                .order_by(DesiredSnapshotRow.generation.desc())
            )
            if row is None:
                raise ControlServiceError("GENERATION_NOT_FOUND", "no desired state published", http_status=404)
            return dict(row.snapshot)

    def post_observation(self, node_id: str, body: dict) -> dict:
        """Store a node's report after validating all of it; a malformed one is 400 and stores nothing."""
        report = validation.parse_report(validation.NodeObservation, body)
        observation = validation.report_dict(report)
        with self._sf() as s:
            self._active_node(s, node_id)
            seq = report.sequence
            row = s.get(ObservationRow, node_id)
            if row is None:
                s.add(ObservationRow(
                    node_id=node_id, sequence=seq, observation=observation, updated_at=self._now(),
                ))
            elif seq >= row.sequence:
                row.sequence = seq
                row.observation = observation
                row.updated_at = self._now()
            s.commit()
        if self.compute is not None:
            self.compute.on_observation(node_id)
        return {"received": True}

    # --- placement (Phase 5, control side) -----------------------------
    def select_placement(
        self, required_vram_bytes: int, count: int = 1, artifact_digest: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Pick a routable node + device(s) for a new engine (DESIGN.md 24 P3).
        Consumes the capacity nodes report: allocatable VRAM, held artifacts,
        running-engine load, and NVLink groups. Preference order: nodes that
        already hold the artifact (locality) > lower load > more headroom. For
        count>1, the devices must share an NVLink group when groups are declared.
        Returns None if nothing fits."""
        best_key: Optional[tuple] = None
        best: Optional[tuple[str, list[str], int]] = None  # (node_id, device_ids, headroom)
        with self._sf() as s:
            for node in s.scalars(self._nodes_q(workspace_id)):
                lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
                if node.revoked or not lease_valid:
                    continue
                obs = s.get(ObservationRow, node.node_id)
                if obs is None:
                    continue
                cap = validation.stored_capacity(obs.observation, node.node_id)
                alloc = dict(cap.get("allocatable_vram_bytes", {}))
                devices = _pick_devices(alloc, cap.get("nvlink_groups", {}), required_vram_bytes, count)
                if devices is None:
                    continue
                headroom = sum(alloc[d] for d in devices)
                artifact_hit = 1 if (artifact_digest and artifact_digest in cap.get("held_artifacts", [])) else 0
                # Prefer live queue depth (real serving load) when the node reports
                # it; fall back to the running-engine count otherwise.
                load = int(cap["queue_depth"]) if "queue_depth" in cap else int(cap.get("running_engines", 0))
                key = (artifact_hit, -load, headroom)  # maximize
                if best_key is None or key > best_key:
                    best_key, best = key, (node.node_id, devices, headroom)
        if best is None:
            return None
        return {
            "node_id": best[0], "device_ids": best[1], "device_id": best[1][0],
            "allocatable_vram_bytes": best[2],
        }

    @staticmethod
    def _nodes_q(workspace_id: Optional[str]):
        q = select(Node)
        return q if workspace_id is None else q.where(Node.workspace_id == workspace_id)

    def crl(self, workspace_id: Optional[str] = None) -> list[dict]:
        """Certificate revocation list: revoked nodes with their cert serial, for
        the TLS-terminating edge to reject at the handshake (DESIGN.md 15.6)."""
        from cryptography import x509

        out: list[dict] = []
        with self._sf() as s:
            for node in s.scalars(self._nodes_q(workspace_id).where(Node.revoked.is_(True))):
                serial: Optional[str] = None
                if node.certificate_pem:
                    try:
                        serial = str(x509.load_pem_x509_certificate(node.certificate_pem.encode()).serial_number)
                    except ValueError:
                        serial = None
                out.append({"node_id": node.node_id, "serial": serial})
        return out

    # --- read models (operator / gateway projection) -------------------
    def serving_projection(self, workspace_id: Optional[str] = None) -> list[dict]:
        """Read-only projection the gateway consumes (DESIGN.md 17). Routable
        endpoints are those with a valid lease and a Ready engine observation."""
        out: list[dict] = []
        with self._sf() as s:
            for node in s.scalars(self._nodes_q(workspace_id)):
                lease_valid = node.lease_expires_at is not None and self._now() < _aware(node.lease_expires_at)
                obs = s.get(ObservationRow, node.node_id)
                engines = validation.stored_observation(obs.observation, node.node_id).get("engines", []) if obs else []
                for e in engines:
                    if e.get("phase") == "Ready" and e.get("endpoint"):
                        out.append({
                            "engine_id": e["engine_id"], "node_id": node.node_id,
                            "endpoint": e["endpoint"], "model": e.get("model", ""),
                            "provider": "managed",
                            "routable": bool(lease_valid) and not node.revoked,
                        })
        return out


    # --- operator console read models ----------------------------------
    # Read-only views for the operator console. They expose identity, lease,
    # generation and capacity metadata only — never private material (public
    # keys, claim nonces, session assertions, raw token values).
    _FAILED_PHASES = frozenset({"Failed", "Quarantined", "Orphaned"})

    def _node_status(self, node: Node) -> str:
        if node.revoked:
            return "revoked"
        if node.lease_expires_at is None:
            return "enrolled"  # approved, no session established yet
        return "online" if self._now() < _aware(node.lease_expires_at) else "offline"

    def _node_summary(self, node: Node, obs: Optional[ObservationRow]) -> dict:
        observation = validation.stored_observation(obs.observation, node.node_id) if obs is not None else {}
        engines = observation.get("engines", []) or []
        cap = observation.get("capacity", {}) or {}
        by_phase: dict[str, int] = {}
        for e in engines:
            phase = str(e.get("phase", "Unknown"))
            by_phase[phase] = by_phase.get(phase, 0) + 1
        alloc = dict(cap.get("allocatable_vram_bytes", {}))
        return {
            "node_id": node.node_id,
            "display_name": node.display_name,
            "labels": dict(node.labels or {}),
            "status": self._node_status(node),
            "revoked": node.revoked,
            "fencing_epoch": node.fencing_epoch,
            "incarnation": node.incarnation,
            "highest_sequence": node.highest_sequence,
            "active_session_id": node.active_session_id,
            "lease_expires_at": _iso(node.lease_expires_at) if node.lease_expires_at else None,
            "desired_generation": node.desired_generation,
            "observed_generation": observation.get("observed_generation") if obs is not None else None,
            "observed_at": _iso(obs.updated_at) if obs is not None and obs.updated_at else None,
            "created_at": _iso(node.created_at) if node.created_at else None,
            "engines": {
                "total": len(engines),
                "ready": by_phase.get("Ready", 0),
                "failed": sum(n for p, n in by_phase.items() if p in self._FAILED_PHASES),
                "by_phase": by_phase,
            },
            "capacity": {
                "devices": len(alloc),
                "allocatable_vram_bytes": sum(alloc.values()),
                "free_ports": cap.get("free_ports"),
                "running_engines": cap.get("running_engines"),
                "queue_depth": cap.get("queue_depth"),
                "held_artifacts": len(cap.get("held_artifacts", []) or []),
            } if cap else None,
        }

    def list_nodes(self, workspace_id: Optional[str] = None) -> list[dict]:
        with self._sf() as s:
            nodes = list(s.scalars(self._nodes_q(workspace_id).order_by(Node.created_at)))
            return [self._node_summary(n, s.get(ObservationRow, n.node_id)) for n in nodes]

    def node_detail(self, node_id: str, audit_limit: int = 50, workspace_id: Optional[str] = None) -> dict:
        from cryptography import x509

        with self._sf() as s:
            node = self._scoped_node(s, node_id, workspace_id)
            obs = s.get(ObservationRow, node_id)
            detail = self._node_summary(node, obs)
            cert: Optional[dict] = None
            if node.certificate_pem:
                try:
                    c = x509.load_pem_x509_certificate(node.certificate_pem.encode())
                    cert = {
                        "serial": str(c.serial_number),
                        "not_valid_before": _iso(c.not_valid_before_utc),
                        "not_valid_after": _iso(c.not_valid_after_utc),
                    }
                except ValueError:
                    cert = None
            desired = s.scalar(
                select(DesiredSnapshotRow).where(DesiredSnapshotRow.node_id == node_id)
                .order_by(DesiredSnapshotRow.generation.desc())
            )
            stops = sorted(
                r.engine_id for r in s.scalars(
                    select(StopAuthorization).where(StopAuthorization.node_id == node_id)
                )
            )
            events = s.scalars(
                select(AuditEvent).where(AuditEvent.node_id == node_id)
                .order_by(AuditEvent.id.desc()).limit(audit_limit)
            )
            detail.update({
                "certificate": cert,
                "observation": validation.stored_observation(obs.observation, node_id) if obs is not None else None,
                "desired": {
                    "generation": desired.generation,
                    "created_at": _iso(desired.created_at) if desired.created_at else None,
                    "snapshot": dict(desired.snapshot),
                } if desired is not None else None,
                "stop_authorized_engine_ids": stops,
                "audit": [_audit_view(e) for e in events],
            })
            return detail

    def list_claims(self, status: Optional[str] = None, workspace_id: Optional[str] = None) -> list[dict]:
        with self._sf() as s:
            q = select(Claim).order_by(Claim.created_at.desc())
            if workspace_id is not None:
                q = q.where(Claim.workspace_id == workspace_id)
            if status:
                q = q.where(Claim.status == status)
            return [
                {
                    "claim_id": c.claim_id,
                    "node_id": c.node_id,
                    "status": c.status,
                    "created_at": _iso(c.created_at) if c.created_at else None,
                }
                for c in s.scalars(q)
            ]

    def list_tokens(self, workspace_id: Optional[str] = None) -> list[dict]:
        """Enrollment tokens by non-reversible reference (hash prefix) only."""
        now = self._now()
        with self._sf() as s:
            out = []
            q = select(EnrollmentToken)
            if workspace_id is not None:
                q = q.where(EnrollmentToken.workspace_id == workspace_id)
            for t in s.scalars(q):
                expired = t.expires_at is not None and now > _aware(t.expires_at)
                out.append({
                    "token_ref": t.token_hash[:12],
                    "auto_approve": t.auto_approve,
                    "status": "used" if t.used else ("expired" if expired else "active"),
                    "expires_at": _iso(t.expires_at) if t.expires_at else None,
                })
            out.sort(key=lambda r: r["expires_at"] or "", reverse=True)
            return out

    def list_audit(
        self, limit: int = 100, node_id: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        with self._sf() as s:
            q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
            if node_id:
                q = q.where(AuditEvent.node_id == node_id)
            if workspace_id is not None:
                q = q.where(AuditEvent.workspace_id == workspace_id)
            return [_audit_view(e) for e in s.scalars(q)]

    def overview(self, workspace_id: Optional[str] = None) -> dict:
        with self._sf() as s:
            inc = self._incarnation(s)
            s.commit()
            incarnation, floor = inc.value, inc.epoch_floor
            claims_q = select(Claim).where(Claim.status == "pending")
            if workspace_id is not None:
                claims_q = claims_q.where(Claim.workspace_id == workspace_id)
            pending = len(list(s.scalars(claims_q)))
        nodes = self.list_nodes(workspace_id)
        projection = self.serving_projection(workspace_id)
        by_status: dict[str, int] = {}
        for n in nodes:
            by_status[n["status"]] = by_status.get(n["status"], 0) + 1
        return {
            "incarnation": incarnation,
            "epoch_floor": floor,
            "nodes": {"total": len(nodes), "by_status": by_status},
            "claims_pending": pending,
            "engines": {
                "total": sum(n["engines"]["total"] for n in nodes),
                "ready": sum(n["engines"]["ready"] for n in nodes),
                "failed": sum(n["engines"]["failed"] for n in nodes),
            },
            "allocatable_vram_bytes": sum(
                n["capacity"]["allocatable_vram_bytes"] for n in nodes
                if n["capacity"] is not None and n["status"] == "online"
            ),
            "routable_endpoints": sum(1 for p in projection if p["routable"]),
            "settings": {
                "lease_ttl_seconds": self._config.lease_ttl_seconds,
                "heartbeat_interval_seconds": self._config.heartbeat_interval_seconds,
                "cert_lifetime_hours": self._config.cert_lifetime_hours,
                "require_mtls": self._config.require_mtls,
            },
        }


def _audit_view(e: AuditEvent) -> dict:
    return {
        "id": e.id,
        "node_id": e.node_id,
        "actor": e.actor or "",
        "kind": e.kind,
        "detail": e.detail,
        "created_at": _iso(e.created_at) if e.created_at else None,
    }


def _cert_pem_from_header(value: str) -> str:
    """Normalize the edge-forwarded client certificate to PEM."""
    text = urllib.parse.unquote(value).strip()
    if "BEGIN CERTIFICATE" in text:
        return text
    try:
        der = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("unreadable client certificate header") from e
    body = base64.encodebytes(der).decode().replace("\n", "")
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n"


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _pick_devices(
    alloc: dict[str, int], groups: dict, required: int, count: int
) -> Optional[list[str]]:
    """Choose `count` devices each with >= required allocatable VRAM. For count>1,
    when NVLink groups are declared the devices must share one group (affinity)."""
    fitting = [d for d, free in alloc.items() if free >= required]
    if not fitting:
        return None
    ranked = sorted(fitting, key=lambda d: alloc[d], reverse=True)  # most headroom first
    if count == 1:
        return [ranked[0]]
    if groups:
        by_group: dict[str, list[str]] = {}
        for d in ranked:
            g = groups.get(d)
            if g is not None:
                by_group.setdefault(g, []).append(d)
        for devs in by_group.values():
            if len(devs) >= count:
                return devs[:count]
        return None  # no single NVLink group has enough fitting devices
    return ranked[:count] if len(ranked) >= count else None
