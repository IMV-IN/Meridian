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

class RateLimitConfig(BaseModel):
    enabled: bool = Field(default=False)
    token_capacity: float = Field(default=1, gt=0)
    token_refill_rate: float = Field(default=1, gt=0)


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


class AuthConfig(BaseModel):
    """API-key authentication. Disabled by default for backward compatibility.

    When enabled, requests must carry a valid ``Authorization: Bearer <key>``
    header; the key maps to an IdentityContext used for logging and (in later
    milestones) rate limiting, cost attribution, and RBAC.
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

    @classmethod
    def from_yaml(cls, path: str) -> MeridianConfig:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    @classmethod
    def from_dict(cls, data: Optional[dict] = None) -> MeridianConfig:
        return cls.model_validate(data or {})
