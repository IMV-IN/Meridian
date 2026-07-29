# Configuration reference

Copy examples into `config.yaml` or a file under `configs/`, then set:

```bash
export MERIDIAN_CONFIG=/path/to/config.yaml   # always UPPERCASE
```

See also: [QUICKSTART.md](./QUICKSTART.md), [enterprise_example.yaml](../configs/enterprise_example.yaml).

---

## Configuration

Create a `config.yaml` (or use one from the `configs/` directory):

```yaml
gateway:
  host: "0.0.0.0"
  port: 8080
  strategy: "least_inflight"  # weighted_round_robin | least_inflight | ewma_latency | token_aware

  # Token-aware routing knobs (only used when strategy: "token_aware").
  # Score = (backend.inflight_cost + request_cost) * (ewma_latency or 1.0)
  # request_cost = prompt_tokens * prefill_weight + max_tokens * decode_weight
  prefill_weight: 1.0          # weight applied to estimated prompt tokens
  decode_weight: 4.0           # weight applied to max_tokens (decode is more expensive per token)
  default_max_tokens: 256      # used when the request doesn't set max_tokens
  token_estimator: "heuristic" # only "heuristic" implemented today

  # Capacity-aware penalties (added to the token_aware base score when backend
  # telemetry exposes the corresponding signal). Default 0.0 = telemetry has
  # no routing effect unless an operator tunes these.
  queue_weight: 0.0            # multiplied by reported queue_depth
  mem_weight: 0.0              # multiplied by reported gpu_mem_util (0.0-1.0)

# Workload tiering (optional, disabled by default). Routes requests to backend
# pools by request shape. A request maps to "long_prompt" when its estimated
# prompt size >= long_prompt_tokens, else "long_decode" when max_tokens >=
# long_decode_tokens, else "default". Precedence is fixed (long_prompt first).
# Each tier maps to backend tags; if the matched tier has no healthy backend,
# Meridian falls back to all healthy backends (reliability over isolation). The
# chosen tier is surfaced on the `x-meridian-tier` response header and in logs.
tiering:
  enabled: false
  long_prompt_tokens: 4000
  long_decode_tokens: 1000
  tiers:
    long_prompt: ["prefill-pool"]
    long_decode: ["decode-pool"]
    default: ["general"]

# Tenant isolation (optional, "shared" by default = pre-0.12 behavior: every
# backend visible to every org). "dedicated" pins orgs listed in `pools` to
# backend tag pools (pool tags ⊆ backend tags — same subset semantics as
# tiering). A pinned org NEVER falls back outside its pool: an empty pool is
# a 503, not a leak onto a neighbor's capacity. Orgs NOT listed (and anonymous
# traffic) are excluded from every reserved pool — any backend matching a
# claimed pool's full tag set. Session affinity remaps a pin whose backend has
# left the org's pool. Empty pool lists, blank org keys/tags, and unknown keys
# in this section fail config validation at load/reload.
isolation:
  mode: "shared"            # shared | dedicated
  pools: {}                 # org_id -> required backend tags
  # pools:
  #   acme-enterprise: ["acme-dedicated", "gpu"]

# Canary rollout (optional, disabled by default). Per-request weighted split
# between the `stable_tags` and `canary_tags` backend pools. The controller
# walks `steps` on a schedule (promotion) and rolls back to weight 0 when the
# canary pool's rolling-window error rate breaches rollback_error_rate with at
# least rollback_min_samples observations (demotion). A pool with no eligible
# backend spills to the other side (availability beats split fidelity); if
# NEITHER pool matches a backend for a model (e.g. untagged deployment),
# routing falls back to all visible backends with a warning rather than
# 503-ing. Dedicated-mode pinned orgs bypass the rollout (pool containment
# wins). Session pins onto a rolled-back canary pool remap immediately; pins
# survive ordinary weight tuning. State: GET /meridian/status ("canary" block),
# meridian_canary_weight gauge, meridian_canary_rollbacks_total counter.
canary:
  enabled: false
  canary_tags: ["canary"]   # tag set of the candidate pool
  stable_tags: ["stable"]   # tag set of the incumbent pool
  start_weight: 0.0         # % of new-session traffic on the canary pool
  steps: []                 # promotion schedule: weight + duration_s (null = hold)
  # steps:
  #   - {weight: 10.0, duration_s: 600}
  #   - {weight: 50.0, duration_s: 1800}
  #   - {weight: 100.0, duration_s: null}
  rollback_error_rate: 0.5  # fraction of 5xx outcomes in the window
  rollback_min_samples: 10  # don't roll back on fewer samples (startup flaps)
  window_s: 60.0            # rolling outcome window
  tick_s: 5.0               # controller cycle period

# Session affinity (optional, disabled by default). Pins a session to one backend
# for KV-cache reuse. Requests carrying the `header` route to the same backend
# while it stays healthy. Sliding TTL: each use refreshes the idle timeout.
# `max_sessions` bounds memory; `sweep_interval_s` controls background eviction.
# If the pinned backend becomes unhealthy, requests remap to another healthy
# backend. Affinity state is surfaced via `x-meridian-session-route` header.
session_affinity:
  enabled: false
  header: "x-meridian-session"
  ttl_s: 600.0
  sweep_interval_s: 60.0
  max_sessions: 100000

# API-key authentication (optional, disabled by default). When enabled, every
# request to /v1/* must carry Authorization: Bearer <key>. Keys are matched
# against this list; unrecognised or missing keys get HTTP 401. Each key maps
# to an identity (org_id required, team_id and user_id optional). Duplicate
# keys are rejected at config load. The /metrics, /meridian/*, and /ui
# endpoints are always open (no auth gate). Key format: mrdn_ followed by
# 20-40 alphanumeric characters.
#
# Optional per-key `key_id` overrides the non-secret prefix identifier used in
# logs, budgets.keys, and usage queries (defaults to the key's first 13 chars).
# `keys_file` (see DEPLOY.md) holds keys outside the main config; when set,
# GET/POST/DELETE /meridian/keys manage those file keys live (admin-only: role
# admin or ops_admin; the raw key is returned exactly once on POST) — see
# OPS_RUNBOOK.md "Auth & keys". Inline `keys:` entries are NOT API-deletable.
auth:
  enabled: false
  # keys_file: /secrets/keys.yaml   # durable key store (managed via API)
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      team_id: "eng"
      user_id: "alice"
    - key: "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
      org_id: "acme"
      # key_id: "svc-embedder"   # optional explicit non-secret id

health:
  interval_s: 5
  timeout_s: 2
  fail_threshold: 2
  success_threshold: 1

# Upstream HTTP timeouts (seconds). Global defaults below preserve historical
# behavior; every key is optional and can also be overridden per backend under
# `backends[].timeout` (a backend key wins; unset keys inherit the global).
# `stream_read` only applies to streaming (SSE) requests and defaults to
# `read` — set it to close stalled streams gracefully (client gets an SSE
# error event + [DONE], and the request is recorded as 504 stream_read_timeout).
timeouts:
  connect: 5.0
  read: 300.0
  write: 5.0
  pool: 5.0
  stream_read: null    # null = use `read` for streams

# Resilience (optional, all off by default = historical behavior).
# - max_retries: retry transport errors (connection/timeout — no response was
#   received). Safe window only: non-stream requests, and the OPEN phase of
#   streams (before the first byte). A stream that has started NEVER retries —
#   delivered tokens can't be un-sent; stalled/broken streams end gracefully
#   (SSE error event + [DONE]) instead. Backoff = retry_backoff_base * 2^n.
#   Retried attempts are counted in meridian_upstream_retries_total{backend}.
# - circuit_breaker: per backend. Opens after failure_threshold consecutive
#   upstream failures; while open, requests are rejected pre-flight with 503
#   ({"error":{"type":"meridian_circuit_open"}}) without touching the backend.
#   After open_seconds a single half-open probe is admitted; success closes
#   the circuit, failure re-opens it. State is visible in /meridian/status
#   and via meridian_backend_circuit_open{backend}.
resilience:
  max_retries: 0
  retry_backoff_base: 0.1
  circuit_breaker:
    enabled: false
    failure_threshold: 5
    open_seconds: 30.0

logging:
  level: "INFO"
  jsonl_path: "./meridian_requests.jsonl"

backends:
  - name: "fast"
    url: "http://backend-fast:9001"
    engine: "vllm"
    model: "demo-model"
    weight: 80
    tags: ["fast"]
    health_endpoint: "/v1/models"
    # Optional per-backend timeout override (fields merge over `timeouts:`).
    timeout:
      read: 60.0
      stream_read: 10.0
    # Scale-to-zero (optional, off by default). After N minutes without request
    # traffic the backend is marked idle: excluded from routing, health checks
    # paused (a scaled-to-zero pod SHOULD fail them), and meridian_backend_idle
    # = 1 on /metrics so external scalers (KEDA) can drive replicas to zero.
    # The first request matching this backend's model/tags wakes it (and may
    # see 502s while the engine cold-starts — see resilience.max_retries).
    idle_timeout_min: 30
    # Optional: tell Meridian to scrape capacity signals from the backend.
    # Failures here NEVER mark the backend unhealthy — telemetry is purely a
    # routing-preference signal and falls back safely when missing.
    telemetry:
      type: "json"
      url: "http://backend-fast:9001/stats"
      interval_s: 5.0
      timeout_s: 2.0

  - name: "slow"
    url: "http://backend-slow:9002"
    engine: "vllm"
    model: "demo-model"
    weight: 20
    tags: ["cheap"]
    health_endpoint: "/v1/models"
```

Pre-built configs are available in the `configs/` directory:
- `configs/mock_demo.yaml` — mock backends for Docker Compose demo
- `configs/local_gpu.yaml` — single GPU backend (Ollama)
- `configs/dual_backend.yaml` — dual-backend failover testing with delay proxy
- `configs/tiering_demo.yaml` — workload tiering across prefill/decode/general pools

## API-key Authentication

Authentication is **disabled by default** and fully backward compatible — existing deployments without an `auth:` block continue to work unchanged.

When `auth.enabled: true`, every request to `/v1/*` must include a valid `Authorization: Bearer <key>` header. The `/metrics`, `/meridian/*`, and `/ui` endpoints are always open with no auth gate.

**Error responses** follow the OpenAI error shape:

- Missing or malformed header → HTTP 401, `"type": "invalid_request_error"`
- Header present but key not found → HTTP 401, `"type": "authentication_error"`

**Key format:** `mrdn_` followed by 20–40 alphanumeric characters. Each key is mapped to an identity at config load (`org_id` required; `team_id` and `user_id` optional). Duplicate keys are rejected at startup.

### Identity-aware logging

When auth is enabled, the resolved identity is attached to every request's observability output as **metadata only** — the API key itself is never logged. Each JSONL line and audit event carries `org_id` and `team_id` (both `null` when auth is disabled), so operators can attribute traffic per org/team:

```json
{"request_id": "mrdn-...", "model": "demo", "chosen_backend": "vllm-a", "status_code": 200, "org_id": "acme", "team_id": "eng", ...}
```

When auth is enabled, **rate limiting keys on the caller's org** (`org:{org_id}`) instead of source IP, so a tenant shares one bucket no matter which IP its requests arrive from. With auth disabled the limiter falls back to per-IP. The same `rate_limit.token_capacity`/`token_refill_rate` apply per bucket.

### Model access control

Each key may declare an `allowed_models` allow-list. When set, requests for any model outside the list return **HTTP 403** (`"type": "permission_error"`); an empty or absent list leaves the key unrestricted. The gate only applies when auth is enabled.

```yaml
auth:
  enabled: true
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      team_id: "eng"
      allowed_models: ["qwen2.5:0.5b", "demo-model"]  # this key is limited to these
    - key: "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
      org_id: "acme"                                    # no list => all models
```

### Tenant budgets & quotas

Budgets are **disabled by default**. When `budgets.enabled: true`, Meridian meters each authenticated request against configured caps **before** routing to a backend. Metering uses the same estimated request cost as token-aware routing (`prompt_tokens * prefill_weight + max_tokens * decode_weight`) plus a request count — no response-body parsing, so streaming stays zero-copy.

Caps cascade **org → team → user → key**. A request debits every applicable level; the first exhausted level returns **HTTP 429** (`"type": "rate_limit_exceeded"`) with `Retry-After` until the UTC period rolls (daily `YYYY-MM-DD` / monthly `YYYY-MM`). Scope keys:

| Level | Config map | Key format |
|---|---|---|
| org | `budgets.orgs` | `org_id` |
| team | `budgets.teams` | `{org_id}/{team_id}` |
| user | `budgets.users` | `{org_id}/{user_id}` |
| key | `budgets.keys` | `{key_id}` (the non-secret prefix id) |

```yaml
budgets:
  enabled: true
  store: sqlite                    # or memory (ephemeral / tests)
  sqlite_path: ./meridian_usage.db
  orgs:
    acme:
      daily:
        tokens: 1000000
        requests: 5000
      monthly:
        tokens: 20000000
  teams:
    acme/eng:
      daily:
        tokens: 200000
  users:
    acme/alice:
      daily:
        requests: 200
  keys:
    mrdn_OrgKey00:                 # per-key caps (Phase 3): one chatty key can
      daily:                       # blow its own budget without tripping the org
        tokens: 50000

# Token-bucket rate limiting (not budget caps). Scopes, checked in order:
#   global (rate_limit.global_limit)   — one fleet-wide bucket (alias `global:`)
#   model  (rate_limit.models[model])  — one bucket per listed model id
#   org    (rate_limit.org_overrides)  — per org (fallback: client IP)
# A request is checked against EVERY applicable scope first and only consumes
# when all admit — a rejection never burns another scope's token. Rejections
# land on meridian_ratelimit_rejections_total{scope=global|model|org|ip}.
rate_limit:
  enabled: true
  token_capacity: 100
  token_refill_rate: 10
  global_limit:                  # optional fleet-wide bucket
    token_capacity: 1000
    token_refill_rate: 100
  models:                        # optional per-model buckets
    big-expensive-model:
      token_capacity: 5
      token_refill_rate: 0.5
  org_overrides:
    acme:
      token_capacity: 20
      token_refill_rate: 5
```

Rejections increment `meridian_budget_rejections_total{level,period}` (never labeled by tenant id). Auth must be enabled for budgets to apply — without an identity there is no tenant to meter.

> **Scope:** the identity keystone (auth, identity logging, per-org rate limiting, model access, tenant budgets) is complete. See [`docs/ship.md`](docs/ship.md).

### PII detection & redaction (India pack)

**Disabled by default.** When `pii.enabled: true`, Meridian scans **request** message text only (response bodies are not scanned in v0.7) for:

| Entity | Notes |
|---|---|
| Aadhaar | 12 digits + **Verhoeff** checksum (rejects random 12-digit strings) |
| PAN | `AAAAA9999A` |
| GSTIN | 15-char GSTIN pattern |
| IFSC | Bank IFSC |
| UPI | `user@handle` |
| Indian mobile | 10-digit starting 6–9, optional `+91` |

**Policies** (global `pii.policy`, optional per-key `pii_policy` override):

| Policy | Behaviour |
|---|---|
| `block` | HTTP 400; request never reaches a backend |
| `redact_and_replace` | Mask PII in messages, then forward |
| `redact_for_logs` / `audit_only` | Forward raw; record **counts by type** only in JSONL/audit |

**Security rules:** matched values are never written to JSONL, audit events, metrics labels, or error messages. Prometheus: `meridian_pii_detections_total{entity,policy}`.

```yaml
pii:
  enabled: true
  policy: redact_and_replace   # or block | redact_for_logs | audit_only
  entities: []                 # empty = all types

auth:
  enabled: true
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      pii_policy: block          # optional override for this key
```

### Quick curl examples

```bash
# Without a key — returns 401
curl -i http://localhost:8080/v1/models

# HTTP/1.1 401 Unauthorized
# {"error": {"message": "Missing or malformed Authorization header", "type": "invalid_request_error"}}

# With a valid key — returns 200
curl -i http://localhost:8080/v1/models \
  -H "Authorization: Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"

# HTTP/1.1 200 OK
# {"object":"list","data":[...]}

# Chat completions also require the header when auth is enabled
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
```

## Pre-built configs

| File | Use |
|------|-----|
| `configs/mock_demo.yaml` | Docker Compose quickstart |
| `configs/local_gpu.yaml` | Ollama / single GPU |
| `configs/enterprise_example.yaml` | Auth + budgets + cost template |
| `configs/poc_design_partner.yaml` | Multi-tenant PoC |
