"""Meridian Compute: per-member GPU-hour quotas and usage groups

Additive only: four nullable columns, no data is touched.

Revision ID: d5f1a7c93e08
Revises: c4e8b2a61f37
Create Date: 2026-11-11 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5f1a7c93e08"
down_revision: Union[str, None] = "c4e8b2a61f37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memberships", sa.Column("group", sa.String(64), nullable=True))
    op.add_column("memberships", sa.Column("gpu_hour_quota", sa.Integer(), nullable=True))
    op.add_column("workspaces", sa.Column("default_gpu_hour_quota", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "default_gpu_hour_quota")
    op.drop_column("memberships", "gpu_hour_quota")
    op.drop_column("memberships", "group")
