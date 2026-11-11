"""Meridian Compute: record when the control plane placed each job replica

Additive only: one nullable column. Replicas placed before it existed get the
time of the next scheduling pass, the first moment this control plane saw them.

Revision ID: f2b6d8e41a53
Revises: e8a3c5d17b94
Create Date: 2026-11-11 20:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b6d8e41a53"
down_revision: Union[str, None] = "e8a3c5d17b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("compute_job_replicas", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_job_replicas", "scheduled_at")
