"""Connection-pool configuration (fleet-scale hardening).

SQLite (dev/test) keeps its thread-safe connect args and no pool sizing; other
backends (Postgres) get a sized, pre-pinged, recycling pool.
"""

from __future__ import annotations

from meridian_control.db import engine_kwargs


def test_sqlite_gets_thread_safe_args_no_pool_sizing():
    kw = engine_kwargs("sqlite:///x.db", pool_size=20, max_overflow=40)
    assert kw["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in kw  # SQLite pool ignores sizing


def test_postgres_gets_sized_pool():
    kw = engine_kwargs("postgresql+psycopg://u:p@h/db", pool_size=20, max_overflow=40)
    assert kw["pool_size"] == 20
    assert kw["max_overflow"] == 40
    assert kw["pool_pre_ping"] is True
    assert kw["pool_recycle"] == 1800
    assert "connect_args" not in kw
