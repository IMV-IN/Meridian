"""Control-plane settings. SQLite by default; Postgres via MERIDIAN_CONTROL_DB_URL.

Every setting is read from the environment so secrets (OAuth client secret,
SMTP password, session key) never live in files committed to a repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> tuple[str, ...]:
    return tuple(v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip())


@dataclass
class ControlConfig:
    db_url: str = "sqlite:///meridian_control.db"
    ca_dir: Path = Path("./ca")
    lease_ttl_seconds: int = 30
    heartbeat_interval_seconds: int = 10
    cert_lifetime_hours: int = 24
    # When true, post-enrollment calls must carry a CA-issued node cert whose SAN
    # node_id matches the path (DESIGN.md 15.6). The TLS-terminating edge verifies
    # the chain and forwards the cert in `client_cert_header` (url-escaped PEM, or
    # base64 DER as Caddy emits it); the app must be reachable only through that
    # edge. Default off for dev/private nets.
    require_mtls: bool = False
    client_cert_header: str = "x-client-cert"
    # SQLAlchemy connection pool (Postgres at fleet scale; ignored for SQLite).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # Browser origins allowed to call the API (the operator console when it is
    # served from the gateway's origin). Empty = no CORS headers (default).
    cors_origins: tuple[str, ...] = ()

    # --- Meridian Cloud (multi-tenant SaaS mode) -----------------------------
    # Off by default: a standalone control plane behaves exactly as before.
    cloud: bool = False
    # Unauthenticated /admin/* operator API. Defaults to on in standalone mode and
    # OFF in cloud mode (tenants use the session-authenticated /api/w/* instead).
    legacy_admin: Optional[bool] = None
    # Public origin users reach (OAuth redirect + cookie Secure flag derive from it).
    public_url: str = "http://localhost:8080"
    # URL GPU nodes use (the mTLS listener), shown in install snippets.
    node_url: str = ""
    # HMAC key for sign-in codes and OAuth state. Empty = generated once and
    # stored next to the CA (ca_dir/session.key), so it survives restarts.
    secret_key: str = ""
    # "open": anyone with a verified Google/email identity may sign up.
    # "invite": only invited emails, allowed domains and admin_emails.
    signup: str = "open"
    allowed_email_domains: tuple[str, ...] = ()
    admin_emails: tuple[str, ...] = ()
    session_ttl_days: int = 14
    session_max_days: int = 30
    google_client_id: str = ""
    google_client_secret: str = ""
    # Email codes: SMTP when smtp_host is set, otherwise a console mailer that
    # logs codes (and writes them to dev_mail_outbox) — development only.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | ssl | none
    mail_from: str = ""
    dev_mail_outbox: str = ""
    # Trust X-Forwarded-For from the reverse proxy (Caddy) for client IPs.
    trust_proxy_headers: bool = False
    # Built console bundle to serve at /ui (default: the meridian package's ui/dist).
    console_dir: str = ""
    # Gateway package (wheel + .sha256) the one-line installer downloads from
    # this server (/download/*). The control image builds it at /opt/meridian/downloads.
    downloads_dir: str = "/opt/meridian/downloads"
    # --- Meridian Compute (marketplace, billing, jobs) -----------------------
    # Meridian's fee on top of the provider's price, in basis points (200 = 2%).
    market_fee_bps: int = 200
    # A rental posts ledger entries at most this often (and once when it ends).
    meter_interval_seconds: int = 600
    # Background pass that meters rentals, enforces funds and timeouts, and
    # places queued jobs. 0 disables it (tests drive `tick()` directly).
    compute_tick_seconds: int = 30
    # A running replica whose node has been silent this long is declared lost.
    node_lost_seconds: int = 300
    # Gateway status reports kept per gateway (bytes of JSON).
    max_report_bytes: int = 256 * 1024
    # A placed job replica must leave Pending/Starting within this long (image pulls
    # included), measured on the control plane's clock, or the job fails.
    job_start_deadline_seconds: int = 1800
    # Request body limits (bytes), enforced while the body streams in, chunked or not.
    max_body_bytes: int = 1024 * 1024
    # Routes anyone can call without signing in (/auth, /oauth, /api/public, enrollment).
    max_unauthenticated_body_bytes: int = 64 * 1024
    # A node's observation (it carries up to 8 KiB of log tail per job replica).
    max_observation_bytes: int = 4 * 1024 * 1024
    extra: dict = field(default_factory=dict)

    @property
    def legacy_admin_enabled(self) -> bool:
        return (not self.cloud) if self.legacy_admin is None else self.legacy_admin

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @classmethod
    def from_env(cls) -> "ControlConfig":
        cors = os.environ.get("MERIDIAN_CONTROL_CORS_ORIGINS", "")
        legacy = os.environ.get("MERIDIAN_CONTROL_LEGACY_ADMIN")
        return cls(
            cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
            db_url=os.environ.get("MERIDIAN_CONTROL_DB_URL", cls.db_url),
            ca_dir=Path(os.environ.get("MERIDIAN_CONTROL_CA_DIR", "./ca")),
            lease_ttl_seconds=int(os.environ.get("MERIDIAN_CONTROL_LEASE_TTL", "30")),
            require_mtls=_flag("MERIDIAN_CONTROL_REQUIRE_MTLS"),
            client_cert_header=os.environ.get("MERIDIAN_CONTROL_CLIENT_CERT_HEADER", cls.client_cert_header),
            db_pool_size=int(os.environ.get("MERIDIAN_CONTROL_DB_POOL_SIZE", "5")),
            db_max_overflow=int(os.environ.get("MERIDIAN_CONTROL_DB_MAX_OVERFLOW", "10")),
            cloud=_flag("MERIDIAN_CONTROL_CLOUD"),
            legacy_admin=None if legacy in (None, "") else _flag("MERIDIAN_CONTROL_LEGACY_ADMIN"),
            public_url=os.environ.get("MERIDIAN_CONTROL_PUBLIC_URL", cls.public_url).rstrip("/"),
            node_url=os.environ.get("MERIDIAN_CONTROL_NODE_URL", "").rstrip("/"),
            secret_key=os.environ.get("MERIDIAN_CONTROL_SECRET_KEY", ""),
            signup=os.environ.get("MERIDIAN_CONTROL_SIGNUP", "open").strip().lower(),
            allowed_email_domains=_list("MERIDIAN_CONTROL_ALLOWED_EMAIL_DOMAINS"),
            admin_emails=_list("MERIDIAN_CONTROL_ADMIN_EMAILS"),
            session_ttl_days=int(os.environ.get("MERIDIAN_CONTROL_SESSION_TTL_DAYS", "14")),
            google_client_id=os.environ.get("MERIDIAN_CONTROL_GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("MERIDIAN_CONTROL_GOOGLE_CLIENT_SECRET", ""),
            downloads_dir=os.environ.get("MERIDIAN_CONTROL_DOWNLOADS_DIR", cls.downloads_dir),
            smtp_host=os.environ.get("MERIDIAN_CONTROL_SMTP_HOST", ""),
            smtp_port=int(os.environ.get("MERIDIAN_CONTROL_SMTP_PORT", "587")),
            smtp_username=os.environ.get("MERIDIAN_CONTROL_SMTP_USERNAME", ""),
            smtp_password=os.environ.get("MERIDIAN_CONTROL_SMTP_PASSWORD", ""),
            smtp_security=os.environ.get("MERIDIAN_CONTROL_SMTP_SECURITY", "starttls").strip().lower(),
            mail_from=os.environ.get("MERIDIAN_CONTROL_MAIL_FROM", ""),
            dev_mail_outbox=os.environ.get("MERIDIAN_CONTROL_DEV_MAIL_OUTBOX", ""),
            trust_proxy_headers=_flag("MERIDIAN_CONTROL_TRUST_PROXY_HEADERS"),
            console_dir=os.environ.get("MERIDIAN_CONTROL_CONSOLE_DIR", ""),
            market_fee_bps=int(os.environ.get("MERIDIAN_CONTROL_MARKET_FEE_BPS", "200")),
            meter_interval_seconds=int(os.environ.get("MERIDIAN_CONTROL_METER_INTERVAL", "600")),
            compute_tick_seconds=int(os.environ.get("MERIDIAN_CONTROL_COMPUTE_TICK", "30")),
            node_lost_seconds=int(os.environ.get("MERIDIAN_CONTROL_NODE_LOST_SECONDS", "300")),
            job_start_deadline_seconds=int(os.environ.get("MERIDIAN_CONTROL_JOB_START_DEADLINE", "1800")),
            max_body_bytes=int(os.environ.get("MERIDIAN_CONTROL_MAX_BODY_BYTES", str(1024 * 1024))),
            max_unauthenticated_body_bytes=int(
                os.environ.get("MERIDIAN_CONTROL_MAX_UNAUTHENTICATED_BODY_BYTES", str(64 * 1024))
            ),
            max_observation_bytes=int(os.environ.get("MERIDIAN_CONTROL_MAX_OBSERVATION_BYTES", str(4 * 1024 * 1024))),
        )
