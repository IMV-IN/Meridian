"""Pydantic configuration models for Meridian."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    strategy: str = "least_inflight"

    # Token-aware routing knobs (only consulted when strategy == "token_aware").
    prefill_weight: float = Field(default=1.0, ge=0.0)
    decode_weight: float = Field(default=4.0, ge=0.0)
    default_max_tokens: int = Field(default=256, ge=1)
    token_estimator: str = "heuristic"

    # Capacity-aware penalties. Added to the token_aware base score when the
    # corresponding telemetry signal is present. Defaults of 0.0 mean the
    # routing decision does not change unless an operator explicitly tunes
    # them, even if telemetry is being collected.
    queue_weight: float = Field(default=0.0, ge=0.0)
    mem_weight: float = Field(default=0.0, ge=0.0)

    # Max request body size for /v1/chat/completions (Milestone K). Default 10 MiB.
    max_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)


class BackendTelemetryConfig(BaseModel):
    """Per-backend telemetry source configuration.

    Currently only the ``json`` type is implemented; the contract is that the
    URL returns a JSON body with optional ``queue_depth`` (int >= 0),
    ``tokens_per_sec`` (float > 0), and ``gpu_mem_util`` (float in [0, 1]).
    """

    type: str = "json"
    url: str
    interval_s: float = Field(default=5.0, gt=0.0)
    timeout_s: float = Field(default=2.0, gt=0.0)


class HealthConfig(BaseModel):
    interval_s: float = 5.0
    timeout_s: float = 2.0
    fail_threshold: int = 2
    success_threshold: int = 1


class LoggingConfig(BaseModel):
    level: str = "INFO"
    jsonl_path: str = "./meridian_requests.jsonl"


class BackendConfig(BaseModel):
    name: str
    url: str
    engine: str = "vllm"
    model: str = ""
    weight: int = Field(default=1, ge=1)
    tags: List[str] = Field(default_factory=list)
    health_endpoint: str = "/v1/models"
    telemetry: Optional[BackendTelemetryConfig] = None
    # Optional upstream Authorization value (never the client Meridian key).
    auth_header: Optional[str] = None


class OrgRateLimitOverride(BaseModel):
    """Per-org token-bucket knobs (not budget caps)."""

    token_capacity: Optional[float] = Field(default=None, gt=0)
    token_refill_rate: Optional[float] = Field(default=None, gt=0)


class RateLimitConfig(BaseModel):
    enabled: bool = Field(default=False)
    token_capacity: float = Field(default=1, gt=0)
    token_refill_rate: float = Field(default=1, gt=0)
    # Bounded store (Milestone K) — idle TTL + max keys prevent unbounded growth.
    max_buckets: int = Field(default=100_000, ge=1)
    idle_ttl_s: float = Field(default=3600.0, gt=0.0)
    sweep_interval_s: float = Field(default=60.0, gt=0.0)
    # Per-org capacity/refill overrides (moved off budgets — clear product boundary).
    org_overrides: Dict[str, OrgRateLimitOverride] = Field(default_factory=dict)


class AuditBusConfig(BaseModel):
    """Configuration for the async audit event bus (Kafka/Redpanda)."""

    enabled: bool = Field(default=False)
    bootstrap_servers: str = "localhost:9092"
    topic: str = "meridian-audit-logs"
    client_id: str = "meridian-gateway"

class TieringConfig(BaseModel):
    """Workload tiering: route requests to backend pools by request shape.

    Disabled by default. When enabled, a request whose estimated prompt size is
    >= ``long_prompt_tokens`` maps to the ``long_prompt`` tier; else if its
    ``max_tokens`` is >= ``long_decode_tokens`` it maps to ``long_decode``; else
    ``default``. ``long_prompt`` is checked first (fixed precedence). Each tier
    name maps to a list of backend tags used for eligibility filtering.
    """

    enabled: bool = Field(default=False)
    long_prompt_tokens: int = Field(default=4000, ge=1)
    long_decode_tokens: int = Field(default=1000, ge=1)
    tiers: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "long_prompt": ["prefill-pool"],
            "long_decode": ["decode-pool"],
            "default": ["general"],
        }
    )

    @field_validator("tiers")
    @classmethod
    def _require_three_buckets(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        required = {"long_prompt", "long_decode", "default"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"tiering.tiers must define {sorted(required)}; missing {sorted(missing)}")
        return v


class SessionAffinityConfig(BaseModel):
    """KV-affinity lite: pin a session to one backend while it stays healthy.

    Disabled by default. When enabled, requests carrying the ``header`` route
    consistently to the same backend. Sliding TTL: each use refreshes the idle
    expiry. ``max_sessions`` bounds memory; ``sweep_interval_s`` controls the
    background eviction cadence.
    """

    enabled: bool = Field(default=False)
    header: str = "x-meridian-session"
    ttl_s: float = Field(default=600.0, gt=0.0)
    sweep_interval_s: float = Field(default=60.0, gt=0.0)
    max_sessions: int = Field(default=100_000, ge=1)


class KeyConfig(BaseModel):
    """A single API key mapped to an identity."""

    key: str = Field(pattern=r"^mrdn_[A-Za-z0-9]{20,40}$")
    org_id: str = Field(min_length=1)
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    # Model allow-list. Empty = all models allowed (backward compatible).
    allowed_models: List[str] = Field(default_factory=list)
    # Optional PII policy override for this key (Milestone L). None = use global.
    pii_policy: Optional[str] = None


class AuthConfig(BaseModel):
    """API-key authentication. Disabled by default for backward compatibility.

    When enabled, requests must carry a valid ``Authorization: Bearer <key>``
    header; the key maps to an IdentityContext used for logging, rate limiting,
    model access, and budgets.
    """

    enabled: bool = Field(default=False)
    keys: List[KeyConfig] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def _no_duplicate_keys(cls, v: List[KeyConfig]) -> List[KeyConfig]:
        seen = [kc.key for kc in v]
        if len(seen) != len(set(seen)):
            raise ValueError("auth.keys contains duplicate key values")
        return v


class PeriodCaps(BaseModel):
    """Optional token/request caps for one period window (daily or monthly)."""

    tokens: Optional[float] = Field(default=None, gt=0)
    requests: Optional[float] = Field(default=None, gt=0)


class ScopeBudget(BaseModel):
    """Caps for one tenant scope (org, team, or user). Daily/monthly only.

    Per-org rate-limit overrides live under ``rate_limit.org_overrides``, not here.
    """

    daily: Optional[PeriodCaps] = None
    monthly: Optional[PeriodCaps] = None


class BudgetConfig(BaseModel):
    """Tenant budgets & quotas. Disabled by default (no block = unchanged).

    Caps are declared per scope id:
    - ``orgs``: key = org_id
    - ``teams``: key = ``{org_id}/{team_id}``
    - ``users``: key = ``{org_id}/{user_id}``

    ``store`` is ``sqlite`` (default, survives restart) or ``memory`` (tests).
    """

    enabled: bool = Field(default=False)
    store: str = Field(default="sqlite")
    sqlite_path: str = "./meridian_usage.db"
    orgs: Dict[str, ScopeBudget] = Field(default_factory=dict)
    teams: Dict[str, ScopeBudget] = Field(default_factory=dict)
    users: Dict[str, ScopeBudget] = Field(default_factory=dict)

    @field_validator("store")
    @classmethod
    def _store_kind(cls, v: str) -> str:
        if v not in ("sqlite", "memory"):
            raise ValueError("budgets.store must be 'sqlite' or 'memory'")
        return v


class PiiConfig(BaseModel):
    """Request-path PII detection (India pack). Disabled by default.

    Policies: ``block``, ``redact_and_replace``, ``redact_for_logs``, ``audit_only``.
    ``entities`` empty = all supported types. Matched values are never logged.
    """

    enabled: bool = Field(default=False)
    policy: str = Field(default="redact_and_replace")
    entities: List[str] = Field(default_factory=list)

    @field_validator("policy")
    @classmethod
    def _policy_ok(cls, v: str) -> str:
        allowed = {"block", "redact_and_replace", "redact_for_logs", "audit_only"}
        if v not in allowed:
            raise ValueError(f"pii.policy must be one of {sorted(allowed)}")
        return v


class MeridianConfig(BaseModel):
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    backends: List[BackendConfig] = Field(default_factory=list)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    audit_bus: AuditBusConfig = Field(default_factory=AuditBusConfig)
    tiering: TieringConfig = Field(default_factory=TieringConfig)
    session_affinity: SessionAffinityConfig = Field(default_factory=SessionAffinityConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    pii: PiiConfig = Field(default_factory=PiiConfig)

    @classmethod
    def from_yaml(cls, path: str) -> MeridianConfig:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    @classmethod
    def from_dict(cls, data: Optional[dict] = None) -> MeridianConfig:
        return cls.model_validate(data or {})
