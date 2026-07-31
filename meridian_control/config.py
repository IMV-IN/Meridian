"""Control-plane settings. SQLite by default; Postgres via MERIDIAN_CONTROL_DB_URL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ControlConfig:
    db_url: str = "sqlite:///meridian_control.db"
    ca_dir: Path = Path("./ca")
    lease_ttl_seconds: int = 30
    heartbeat_interval_seconds: int = 10
    cert_lifetime_hours: int = 24

    @classmethod
    def from_env(cls) -> "ControlConfig":
        return cls(
            db_url=os.environ.get("MERIDIAN_CONTROL_DB_URL", cls.db_url),
            ca_dir=Path(os.environ.get("MERIDIAN_CONTROL_CA_DIR", "./ca")),
            lease_ttl_seconds=int(os.environ.get("MERIDIAN_CONTROL_LEASE_TTL", "30")),
        )
