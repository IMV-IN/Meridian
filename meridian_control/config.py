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
    # When true, post-enrollment calls must carry a CA-issued node cert whose SAN
    # node_id matches the path (DESIGN.md 15.6). The TLS-terminating edge verifies
    # the chain and forwards the PEM (url-escaped) in `client_cert_header`; the app
    # must be reachable only through that edge. Default off for dev/private nets.
    require_mtls: bool = False
    client_cert_header: str = "x-client-cert"

    @classmethod
    def from_env(cls) -> "ControlConfig":
        return cls(
            db_url=os.environ.get("MERIDIAN_CONTROL_DB_URL", cls.db_url),
            ca_dir=Path(os.environ.get("MERIDIAN_CONTROL_CA_DIR", "./ca")),
            lease_ttl_seconds=int(os.environ.get("MERIDIAN_CONTROL_LEASE_TTL", "30")),
            require_mtls=os.environ.get("MERIDIAN_CONTROL_REQUIRE_MTLS", "").lower() in ("1", "true", "yes"),
            client_cert_header=os.environ.get("MERIDIAN_CONTROL_CLIENT_CERT_HEADER", cls.client_cert_header),
        )
