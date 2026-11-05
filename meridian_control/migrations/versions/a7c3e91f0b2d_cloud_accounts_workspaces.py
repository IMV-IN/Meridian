"""Meridian Cloud: accounts, workspaces, sessions, gateway tokens and reports

Adds the multi-tenant tables and a nullable workspace_id on the fleet tables.
NULL workspace_id keeps standalone (single-tenant) deployments unchanged.

Revision ID: a7c3e91f0b2d
Revises: dc240ff6c192
Create Date: 2026-11-05 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e91f0b2d"
down_revision: Union[str, None] = "dc240ff6c192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("avatar_url", sa.String(1024), nullable=False),
        sa.Column("google_sub", sa.String(64), nullable=True, unique=True),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("last_login_at", _TZ, nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)

    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("public_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("last_seen_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_auth_sessions_public_id", "auth_sessions", ["public_id"], unique=True)
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "email_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
    )
    op.create_index("ix_email_codes_email", "email_codes", ["email"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("invited_by", sa.String(40), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("accepted_at", _TZ, nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_invitations_workspace_id", "invitations", ["workspace_id"])
    op.create_index("ix_invitations_email", "invitations", ["email"])

    op.create_table(
        "gateway_tokens",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("last_used_at", _TZ, nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_gateway_tokens_workspace_id", "gateway_tokens", ["workspace_id"])

    op.create_table(
        "gateways",
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("gateway_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("token_id", sa.String(40), nullable=False),
        sa.Column("first_seen_at", _TZ, nullable=False),
        sa.Column("last_seen_at", _TZ, nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
    )

    # Workspace scoping on the fleet tables. Plain indexed columns (no FK) so
    # SQLite can add them without a table rebuild.
    for table in ("nodes", "claims", "enrollment_tokens", "audit_events"):
        op.add_column(table, sa.Column("workspace_id", sa.String(40), nullable=True))
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.add_column("audit_events", sa.Column("actor", sa.String(320), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("audit_events", "actor")
    for table in ("nodes", "claims", "enrollment_tokens", "audit_events"):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_column(table, "workspace_id")
    for table in ("gateways", "gateway_tokens", "invitations", "email_codes", "auth_sessions", "memberships", "workspaces", "users"):
        op.drop_table(table)
