"""SQLAlchemy engine/session wiring. Sync sessions, one per request."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def engine_kwargs(db_url: str, pool_size: int = 5, max_overflow: int = 10) -> dict:
    """create_engine kwargs. SQLite gets thread-safe connect args and no pool
    sizing (its pool ignores it); other backends (Postgres at fleet scale) get a
    sized, pre-pinged, recycling connection pool."""
    if db_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}, "future": True}
    return {
        "future": True,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


def make_engine(db_url: str, pool_size: int = 5, max_overflow: int = 10):
    return create_engine(db_url, **engine_kwargs(db_url, pool_size, max_overflow))


def make_session_factory(db_url: str, create_schema: bool = True, pool_size: int = 5, max_overflow: int = 10):
    """Session factory. `create_schema` uses `create_all` for dev/tests; set it
    False in production and manage the schema with Alembic (`meridian-control
    migrate`, or `run_migrations`)."""
    engine = make_engine(db_url, pool_size, max_overflow)
    if create_schema:
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def run_migrations(db_url: str) -> None:
    """Apply Alembic migrations up to head (the production schema path)."""
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).parent
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
