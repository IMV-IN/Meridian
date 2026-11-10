"""Meridian Compute: marketplace offers and rentals, ledger, jobs, API keys

Additive only. Existing fleet, account and gateway rows are untouched; the one
change to an existing table is a new `nodes.inventory` column with a default.

Revision ID: c4e8b2a61f37
Revises: a7c3e91f0b2d
Create Date: 2026-11-10 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b2a61f37"
down_revision: Union[str, None] = "a7c3e91f0b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("nodes", sa.Column("inventory", sa.JSON(), nullable=False, server_default="[]"))

    op.create_table(
        "market_offers",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("node_id", sa.String(128), sa.ForeignKey("nodes.node_id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("gpu_model", sa.String(120), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("vram_bytes_per_gpu", sa.BigInteger(), nullable=False),
        sa.Column("device_ids", sa.JSON(), nullable=False),
        sa.Column("price_paise_per_gpu_hour", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_index("ix_market_offers_workspace_id", "market_offers", ["workspace_id"])
    op.create_index("ix_market_offers_node_id", "market_offers", ["node_id"])
    op.create_index("ix_market_offers_status", "market_offers", ["status"])

    op.create_table(
        "market_rentals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("offer_id", sa.String(40), sa.ForeignKey("market_offers.id"), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("provider_workspace_id", sa.String(40), nullable=False),
        sa.Column("consumer_workspace_id", sa.String(40), nullable=False),
        sa.Column("device_ids", sa.JSON(), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("price_paise_per_gpu_hour", sa.Integer(), nullable=False),
        sa.Column("fee_bps", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("end_reason", sa.String(40), nullable=False),
        sa.Column("release_after_job_id", sa.String(40), nullable=True),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("ended_at", _TZ, nullable=True),
        sa.Column("billed_paise", sa.BigInteger(), nullable=False),
        sa.Column("fee_paise", sa.BigInteger(), nullable=False),
        sa.Column("billed_through", _TZ, nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
    )
    for col in ("offer_id", "node_id", "provider_workspace_id", "consumer_workspace_id", "status"):
        op.create_index(f"ix_market_rentals_{col}", "market_rentals", [col])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("rental_id", sa.String(40), nullable=True),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("memo", sa.String(400), nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_index("ix_ledger_entries_workspace_id", "ledger_entries", ["workspace_id"])
    op.create_index("ix_ledger_entries_rental_id", "ledger_entries", ["rental_id"])

    op.create_table(
        "compute_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env", sa.JSON(), nullable=False),
        sa.Column("replicas", sa.Integer(), nullable=False),
        sa.Column("gpus_per_replica", sa.Integer(), nullable=False),
        sa.Column("min_vram_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expose_port", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("rental_id", sa.String(40), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(1024), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("started_at", _TZ, nullable=True),
        sa.Column("finished_at", _TZ, nullable=True),
    )
    op.create_index("ix_compute_jobs_workspace_id", "compute_jobs", ["workspace_id"])
    op.create_index("ix_compute_jobs_rental_id", "compute_jobs", ["rental_id"])
    op.create_index("ix_compute_jobs_status", "compute_jobs", ["status"])

    op.create_table(
        "compute_job_replicas",
        sa.Column("job_id", sa.String(40), sa.ForeignKey("compute_jobs.id"), primary_key=True),
        sa.Column("rank", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("device_ids", sa.JSON(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("endpoint", sa.String(512), nullable=False),
        sa.Column("log_tail", sa.String(8192), nullable=False),
        sa.Column("started_at", _TZ, nullable=True),
        sa.Column("finished_at", _TZ, nullable=True),
        sa.Column("released", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_compute_job_replicas_node_id", "compute_job_replicas", ["node_id"])
    op.create_index("ix_compute_job_replicas_released", "compute_job_replicas", ["released"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("last_used_at", _TZ, nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])


def downgrade() -> None:
    for table in ("api_keys", "compute_job_replicas", "compute_jobs", "ledger_entries", "market_rentals", "market_offers"):
        op.drop_table(table)
    op.drop_column("nodes", "inventory")
