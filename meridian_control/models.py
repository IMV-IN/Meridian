"""ORM models for durable control-plane state (DESIGN.md 17).

Distinct resources, never collapsed into one Backend object: nodes, enrollment
tokens, claims, desired snapshots, observations, stop authorizations, and the
singleton control-plane incarnation used for restore-safe fencing (17.5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
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


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(String(40), ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | admin | operator | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
