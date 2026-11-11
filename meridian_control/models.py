"""ORM models for durable control-plane state (DESIGN.md 17).

Distinct resources, never collapsed into one Backend object: nodes, enrollment
tokens, claims, desired snapshots, observations, stop authorizations, and the
singleton control-plane incarnation used for restore-safe fencing (17.5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Incarnation(Base):
    __tablename__ = "incarnation"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    value: Mapped[int] = mapped_column(Integer, default=1)
    # Epochs never regress below this floor; a restore runbook raises it past the
    # last-issued high-water mark so a fenced agent can never be re-admitted.
    epoch_floor: Mapped[int] = mapped_column(Integer, default=0)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    auto_approve: Mapped[bool] = mapped_column(default=False)
    used: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Claim(Base):
    __tablename__ = "claims"
    claim_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(128))
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|denied
    workspace_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Node(Base):
    __tablename__ = "nodes"
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    certificate_pem: Mapped[str] = mapped_column(String, default="")
    display_name: Mapped[str] = mapped_column(String(253), default="")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    active_session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    fencing_epoch: Mapped[int] = mapped_column(Integer, default=0)
    incarnation: Mapped[int] = mapped_column(Integer, default=1)
    highest_sequence: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    desired_generation: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # GPU inventory from the node's last heartbeat (model, memory). Read by the
    # marketplace to describe what an offer actually is.
    inventory: Mapped[list] = mapped_column(JSON, default=list)


class DesiredSnapshotRow(Base):
    __tablename__ = "desired_snapshots"
    __table_args__ = (UniqueConstraint("node_id", "generation", name="uq_node_generation"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(128), ForeignKey("nodes.node_id"))
    generation: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ObservationRow(Base):
    __tablename__ = "observations"
    node_id: Mapped[str] = mapped_column(String(128), ForeignKey("nodes.node_id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class StopAuthorization(Base):
    __tablename__ = "stop_authorizations"
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    engine_id: Mapped[str] = mapped_column(String(128), primary_key=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(128), default="")
    workspace_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(320), default="")  # user email / "gateway:<id>" / ""
    kind: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- Meridian Cloud: accounts, workspaces, gateways ---------------------------
# Multi-tenant SaaS mode (MERIDIAN_CONTROL_CLOUD=1). Fleet rows above carry a
# nullable workspace_id: NULL means a standalone (single-tenant) deployment.


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(1024), default="")
    google_sub: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    disabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Monthly GPU-hours each member may use on the workspace's own nodes when the
    # member has no quota of their own. NULL = unlimited.
    default_gpu_hour_quota: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | admin | operator | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Compute usage reporting: a department or lab, and a monthly GPU-hour cap on own-node jobs.
    group: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gpu_hour_quota: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class AuthSession(Base):
    """Browser session. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(16), default="")  # google | email
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class EmailCode(Base):
    """One-time sign-in code. Stored as an HMAC; never in plaintext."""

    __tablename__ = "email_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(16))
    invited_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(default=False)


class GatewayToken(Base):
    """Credential a customer gateway uses to pull its workspace's serving
    projection and push metadata-only status reports. Hash stored, never the token."""

    __tablename__ = "gateway_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(default=False)


class GatewayInstance(Base):
    """Latest status report from one customer gateway process."""

    __tablename__ = "gateways"
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), primary_key=True)
    gateway_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    token_id: Mapped[str] = mapped_column(String(40), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    report: Mapped[dict] = mapped_column(JSON, default=dict)


# --- Meridian Compute: marketplace, billing, jobs -----------------------------
# Money is integer paise (INR). Never a float: a ledger must add up exactly.

PLATFORM_ACCOUNT = "platform"  # ledger account that collects the marketplace fee


class MarketOffer(Base):
    """A provider workspace lists one node's GPUs for rent, as a unit."""

    __tablename__ = "market_offers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(128), ForeignKey("nodes.node_id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    region: Mapped[str] = mapped_column(String(80), default="")
    gpu_model: Mapped[str] = mapped_column(String(120), default="")
    gpu_count: Mapped[int] = mapped_column(Integer)
    vram_bytes_per_gpu: Mapped[int] = mapped_column(BigInteger, default=0)
    device_ids: Mapped[list] = mapped_column(JSON, default=list)
    price_paise_per_gpu_hour: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="listed", index=True)  # listed | paused | retired
    # public: any workspace may rent. private: only the owner and the workspaces in
    # `shared_with` (workspace ids, never slugs) can see or rent it, and it may be free.
    visibility: Mapped[str] = mapped_column(String(8), default="public")
    shared_with: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Rental(Base):
    """A consumer workspace holds an offer's GPUs. Billed per second from
    started_at to ended_at at the price and fee captured when it began."""

    __tablename__ = "market_rentals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    offer_id: Mapped[str] = mapped_column(String(40), ForeignKey("market_offers.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    consumer_workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    device_ids: Mapped[list] = mapped_column(JSON, default=list)
    gpu_count: Mapped[int] = mapped_column(Integer)
    price_paise_per_gpu_hour: Mapped[int] = mapped_column(Integer)
    fee_bps: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | ended
    end_reason: Mapped[str] = mapped_column(String(40), default="")
    release_after_job_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cumulative amounts already posted to the ledger. Each metering pass posts
    # (total due so far) minus these, so rounding never drifts.
    billed_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    fee_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    billed_through: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(320), default="")


class LedgerEntry(Base):
    """Append-only. A workspace's balance is the sum of its entries."""

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)  # or PLATFORM_ACCOUNT
    # topup | rental_charge | platform_fee | rental_earning | fee_revenue | payout | adjustment
    kind: Mapped[str] = mapped_column(String(24))
    amount_paise: Mapped[int] = mapped_column(BigInteger)  # signed: credit > 0, debit < 0
    rental_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    reference: Mapped[str] = mapped_column(String(200), default="")  # payment / payout reference
    memo: Mapped[str] = mapped_column(String(400), default="")
    actor: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Job(Base):
    """A batch job (runs to completion) or a service (long-running, has a port)."""

    __tablename__ = "compute_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="job")  # job | service
    image: Mapped[str] = mapped_column(String(512))
    args: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    replicas: Mapped[int] = mapped_column(Integer, default=1)
    gpus_per_replica: Mapped[int] = mapped_column(Integer, default=1)
    min_vram_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    expose_port: Mapped[bool] = mapped_column(default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=0)
    rental_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    error: Mapped[str] = mapped_column(String(1024), default="")
    created_by: Mapped[str] = mapped_column(String(320), default="")
    # The member whose usage and quota this job counts against, fixed at submit: the
    # signed-in member, or the member who created the API key that submitted it.
    # NULL on jobs from before it was recorded (usage then falls back to `created_by`).
    attributed_to: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class JobReplica(Base):
    __tablename__ = "compute_job_replicas"
    job_id: Mapped[str] = mapped_column(String(40), ForeignKey("compute_jobs.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[str] = mapped_column(String(128), index=True)
    device_ids: Mapped[list] = mapped_column(JSON, default=list)
    port: Mapped[int] = mapped_column(Integer, default=0)
    host: Mapped[str] = mapped_column(String(253), default="")
    phase: Mapped[str] = mapped_column(String(16), default="Pending")
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
    message: Mapped[str] = mapped_column(String(1024), default="")
    endpoint: Mapped[str] = mapped_column(String(512), default="")
    log_tail: Mapped[str] = mapped_column(String(8192), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the control plane placed the replica, on its own clock: start deadlines
    # and the clamp on node-reported times are measured from here.
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # True once the node no longer needs this replica in its desired snapshot:
    # it holds no GPU and its container may be removed.
    released: Mapped[bool] = mapped_column(default=False, index=True)


class ApiKey(Base):
    """Workspace API key for the CLI and automation (`mck_...`). Hash stored."""

    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))  # viewer | operator | admin
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(default=False)
