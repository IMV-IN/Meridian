"""SQLAlchemy engine/session wiring. Sync sessions, one per request."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_url: str):
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args, future=True)


def make_session_factory(db_url: str, create_schema: bool = True):
    """Session factory. `create_schema` uses `create_all` for dev/tests; set it
    False in production and manage the schema with Alembic (`meridian-control
    migrate`, or `run_migrations`)."""
    engine = make_engine(db_url)
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
