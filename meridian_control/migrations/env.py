"""Alembic environment for meridian-control.

The DB URL comes from ControlConfig (MERIDIAN_CONTROL_DB_URL) unless overridden
with `-x db_url=...`. target_metadata is the ORM Base so autogenerate stays in
sync with meridian_control.models.
"""

from __future__ import annotations

from alembic import context

import meridian_control.models  # noqa: F401 - import registers all tables on Base.metadata
from meridian_control.config import ControlConfig
from meridian_control.db import Base, make_engine

target_metadata = Base.metadata


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    ini = context.config.get_main_option("sqlalchemy.url")
    return ini or ControlConfig.from_env().db_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
