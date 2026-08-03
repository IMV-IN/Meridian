"""Alembic migrations build the full schema (the production schema path).

Proves `run_migrations` (used by `meridian-control migrate`) applies cleanly to a
fresh database and that the initial migration stays in sync with the ORM models.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from meridian_control.db import Base, run_migrations


def test_migrations_create_full_schema(tmp_path):
    db_url = f"sqlite:///{tmp_path}/m.db"
    run_migrations(db_url)  # apply head to an empty DB
    tables = set(inspect(create_engine(db_url)).get_table_names())
    expected = set(Base.metadata.tables) | {"alembic_version"}
    missing = expected - tables
    assert not missing, f"migration is missing tables: {missing}"


def test_migrations_are_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path}/m.db"
    run_migrations(db_url)
    run_migrations(db_url)  # second upgrade to head is a no-op, must not error
