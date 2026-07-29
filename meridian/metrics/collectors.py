"""Prometheus metrics collectors for Meridian."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "meridian_requests_total",
    "Total requests proxied",
    ["backend", "model", "status", "stream"],
)

REQUEST_LATENCY = Histogram(
    "meridian_request_latency_ms",
    "Request latency in milliseconds",
    ["backend", "model"],
    buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
)

BACKEND_INFLIGHT = Gauge(
    "meridian_backend_inflight",
    "Currently inflight requests per backend",
    ["backend"],
)

BACKEND_HEALTHY = Gauge(
    "meridian_backend_healthy",
    "Backend health status (1=healthy, 0=unhealthy)",
    ["backend"],
)

# Cardinality-safe: level + period only — never tenant id (CLAUDE.md §5).
BUDGET_REJECTIONS = Counter(
    "meridian_budget_rejections_total",
    "Requests rejected for exceeding a tenant budget cap",
    ["level", "period"],
)

# Cardinality-safe: scope is one of global|model|org|ip — never tenant id.
RATELIMIT_REJECTIONS = Counter(
    "meridian_ratelimit_rejections_total",
    "Requests rejected by token-bucket rate limiting",
    ["scope"],
)

# Canary rollout state (Phase 3). Unlabeled: one rollout per process.
CANARY_WEIGHT = Gauge(
    "meridian_canary_weight",
    "Current canary traffic weight (percent)",
)
CANARY_ROLLBACKS = Counter(
    "meridian_canary_rollbacks_total",
    "Automatic canary rollbacks on error-rate breach",
)

# Cardinality-safe: direction only — never tenant id.
BUDGET_RECONCILES = Counter(
    "meridian_budget_reconciles_total",
    "Budget meter adjustments after actual backend usage",
    ["direction"],  # over | under | equal (equal rare; usually no-op before count)
)

# Cardinality-safe: entity type + policy only — never matched values or tenant id.
PII_DETECTIONS = Counter(
    "meridian_pii_detections_total",
    "PII entities detected on the request path",
    ["entity", "policy"],
)

# model + kind only — org lives in the ledger API (cardinality rule).
TOKENS_TOTAL = Counter(
    "meridian_tokens_total",
    "Actual tokens attributed from backend usage fields",
    ["model", "kind"],
)

# Retries on upstream connection/timeout errors (resilience.max_retries).
UPSTREAM_RETRIES = Counter(
    "meridian_upstream_retries_total",
    "Retried upstream attempts (connection/timeout errors)",
    ["backend"],
)

# Circuit breaker visibility (1 = circuit open, requests rejected pre-flight).
BACKEND_CIRCUIT_OPEN = Gauge(
    "meridian_backend_circuit_open",
    "Backend circuit breaker state (1=open, 0=closed/half-open)",
    ["backend"],
)

# Scale-to-zero (1 = past backends[].idle_timeout_min, excluded from routing).
BACKEND_IDLE = Gauge(
    "meridian_backend_idle",
    "Backend idle marker for external scalers (1=idle, 0=active)",
    ["backend"],
)
