"""Meridian Compute: private listings shared with named workspaces

Additive only: two columns on `market_offers`. Existing listings stay public.

Revision ID: e8a3c5d17b94
Revises: d5f1a7c93e08
Create Date: 2026-11-11 11:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8a3c5d17b94"
down_revision: Union[str, None] = "d5f1a7c93e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("market_offers", sa.Column("visibility", sa.String(8), nullable=False, server_default="public"))
    op.add_column("market_offers", sa.Column("shared_with", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("market_offers", "shared_with")
    op.drop_column("market_offers", "visibility")
