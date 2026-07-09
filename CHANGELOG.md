# Changelog

All notable changes to this project will be documented in this file.

## [0.7.0] - 2026-07-09

### Added

- **PII detection & redaction (Milestone L)** — opt-in India entity pack (`meridian/pii/`): Aadhaar (Verhoeff), PAN, GSTIN, IFSC, UPI, Indian mobile. Policies: `block` (400), `redact_and_replace` (mask before forward), `redact_for_logs` / `audit_only` (forward raw; counts only in audit/JSONL). Global `pii:` config + per-key `pii_policy` override. Request-path only (response scanning deferred). Metrics: `meridian_pii_detections_total{entity,policy}` — never logs matched values.

## [0.6.0] - 2026-07-09

### Added

- **Bounded rate-limit store (Milestone K)** — `RateLimitStore` with idle TTL (default 1h) and max keys (default 100k); sliding expiry on access, sweep drops idle buckets, nearest-expiry eviction when full. Config: `rate_limit.max_buckets`, `idle_ttl_s`, `sweep_interval_s`.
- **Request body size cap** — `gateway.max_body_bytes` (default 10 MiB); oversized `Content-Length` or actual body → HTTP 413.
- **Container hardening** — Dockerfile runs as non-root `meridian` (uid 10001), `HEALTHCHECK` against `/meridian/status`, `python:3.11-slim-bookworm` base.
- **Version consistency CI** — package version must match `meridian/__init__.__version__`; release tags must match `pyproject.toml` version.

### Fixed

- **Stream disconnect cleanup** — request finalize path is fully synchronous (counters, JSONL, audit `enqueue`); client cancel mid-SSE no longer risks skipping inflight decrement or losing the audit event. Disconnect recorded as status `499` / `client_disconnect`.
- Package version bumped to **0.6.0** (was stuck at 0.1.0).

## [0.5.0] - 2026-07-09

### Added

- **Model access control (Milestone I)** — each API key may declare an `allowed_models` list; when set, requests for any other model return HTTP 403 with `"type": "permission_error"` (OpenAI error envelope). An empty/absent list means all models are allowed (backward compatible), and the gate only applies when auth is enabled. The allow-list is carried on `IdentityContext.scopes` (previously reserved) and enforced in the chat handler right after the model is parsed. First half of the v0.5 tenant-governance theme; see `docs/superpowers/specs/2026-06-30-v0.5-tenant-governance-design.md`.
- **Tenant budgets & quotas (Milestone J)** — opt-in org→team→user usage caps. New `budgets:` config block (`enabled` default false) declares daily/monthly limits on estimated **tokens** (`request_ctx.cost`) and **requests** at `orgs` / `teams` / `users` maps. Pluggable `UsageMeter` (`meridian/usage/`): `SqliteUsageMeter` (default, path `budgets.sqlite_path`) and `InMemoryUsageMeter` (tests). Pre-flight `check_and_increment` before routing — exceed any level → HTTP 429 `"type": "rate_limit_exceeded"` with `Retry-After` until period rollover. Per-org rate-limit overrides via `budgets.orgs.<id>.token_capacity` / `token_refill_rate`. Prometheus counter `meridian_budget_rejections_total{level,period}` (never labeled by tenant id). Chat path order is now access → budget → rate-limit → route so 403/budget 429 do not spend rate tokens.

### Changed

- **Chat handler gate order** — model access and budget checks run before the token-bucket rate limiter (was: rate limit before body parse / model access).

## [0.4.0] - 2026-06-30

### Added

- **Per-identity rate limiting (Milestone H)** — when `auth.enabled: true`, the token-bucket rate limiter keys on the caller's org (`org:{org_id}`) instead of source IP, so a tenant shares one bucket regardless of which IP requests arrive from (IP-keying is meaningless behind a VPC load balancer). When auth is disabled, behavior is unchanged (`ip:{ip_address}`); the namespace prefix keeps the two keyspaces from colliding. Same global `token_capacity`/`token_refill_rate` applies per bucket — per-org custom quotas and team-level granularity are a later slice. The chat handler now resolves identity before the rate-limit check (`meridian/api/main.py`).
- **Identity-aware logging (Milestone G)** — when auth is enabled, the resolved identity (`org_id`, `team_id`) is attached to every JSONL request log line and audit event as metadata; the API key itself is never logged. Both fields are `null` when auth is disabled, keeping the record shape stable. The auth middleware now stashes the validated `IdentityContext` on `request.state`; the chat handler reads it and threads `org_id`/`team_id` through `RequestLogger.log` and `AuditEvent.extra`. **Scope:** logging only — per-identity rate limiting is still a later slice.
- **API-key authentication (Milestone F)** — opt-in Bearer-key enforcement on `/v1/*`, config-driven and disabled by default. When `auth.enabled: true`, every request to `/v1/chat/completions` and `/v1/models` must carry a valid `Authorization: Bearer <key>` header; missing or malformed headers return HTTP 401 with `"type": "invalid_request_error"`, unrecognised keys with `"type": "authentication_error"`. Both error shapes follow the OpenAI error envelope (`{"error": {"message": "...", "type": "..."}}`). The `/metrics`, `/meridian/*`, and `/ui` endpoints are always open with no auth gate. Keys are declared in the `auth.keys` list; each key maps to an identity (`org_id` required, `team_id` and `user_id` optional) and must match the pattern `^mrdn_[A-Za-z0-9]{20,40}$`. Duplicate keys are rejected at config load. New `meridian/auth/` package (`keys.py`), `KeyConfig` and `AuthConfig` Pydantic models (in `meridian/config/models.py`), and FastAPI middleware wired in `meridian/api/main.py`. **Scope:** authentication enforcement only — identity-aware logging and per-identity rate limiting are planned for a later slice.

## [0.3.1] - Unreleased

### Added

- **KV-affinity lite (Milestone E)** — session stickiness for KV-cache reuse, config-driven and disabled by default. When enabled, requests carrying the `session_affinity.header` (default `x-meridian-session`) route consistently to the same backend while it remains healthy and serving the model. Session state is an in-memory map with sliding TTL (each use refreshes expiry) and max-sessions cap (evicts nearest-expiry when full). A background sweep task drops expired entries. If the pinned backend becomes unhealthy, the request remaps to another healthy backend (reliability over stickiness). Affinity state is surfaced via the `x-meridian-session-route` response header (`new` / `pinned` / `remapped`), the `session_route` JSONL log field, and `RequestLogger`. New `meridian/router/affinity.py` (`SessionStore`), `SessionAffinityConfig`, and the `_route` helper composing affinity over workload tiering.

## [0.3.0] - Unreleased

### Added

- **Workload tiering (Milestone D)** — route requests to dedicated backend pools by request shape, config-driven and disabled by default. A request maps to `long_prompt` (estimated `prompt_tokens >= long_prompt_tokens`), else `long_decode` (`max_tokens >= long_decode_tokens`), else `default`; each tier name maps to backend tags used for eligibility. Precedence is fixed (`long_prompt` first). If the matched tier's pool has no healthy backend, Meridian logs a warning and falls back to all healthy backends (reliability over isolation). The chosen tier is exposed via the `x-meridian-tier` response header, the `tier` JSONL log field, and the `extra.tier` field on audit-bus events. New `meridian/router/tiering.py` (`derive_tier`), `TieringConfig`, and `configs/tiering_demo.yaml`.

## [0.2.1] - Unreleased

### Added

- **Telemetry adapters** (`meridian/telemetry/`) — backend-side capacity signals (queue depth, GPU memory, tokens/sec) feed into the router. New `BackendTelemetry` dataclass + `TelemetryAdapter` ABC; ships with a generic `JsonTelemetryAdapter` that polls a backend-provided URL returning `{"queue_depth": int?, "tokens_per_sec": float?, "gpu_mem_util": float?}` (all optional).
- **`TelemetryPoller`** — async background task per Meridian instance, polling each opted-in backend at a configurable interval. Wired in alongside `HealthChecker` in lifespan.
- **Capacity-aware penalties on `token_aware`** — score now adds `(queue_depth or 0) * queue_weight + (gpu_mem_util or 0) * mem_weight`. Defaults `queue_weight=0.0`, `mem_weight=0.0` keep existing behavior unchanged unless an operator opts in.
- **Per-backend telemetry config** — new `BackendConfig.telemetry` (optional): `{type: json, url: ..., interval_s: 5.0, timeout_s: 2.0}`.
- **Status + UI surface** — `/meridian/status` now exposes `queue_depth`, `tokens_per_sec`, `gpu_mem_util` per backend. `/ui` renders these on each backend card when present, so an operator can see *"A is healthy but capacity-penalized"* at a glance.

### Architectural invariant

**Health gates eligibility; telemetry tilts preference.** Telemetry fetch failures must never affect a backend's health state — they clear that backend's signals and the router falls back to its base scoring with no capacity penalty. This is enforced in `TelemetryPoller._poll_one` (catches all adapter errors, calls `clear_telemetry()`, never touches health counters) and covered by an explicit negative test.

### Validated by Milestone C DoD test

`test_dod_healthy_but_overloaded_backend_is_avoided` — both backends report healthy; backend A reports `queue_depth=100`, backend B reports `queue_depth=0`; with `queue_weight` tuned, every new request routes to B; `/meridian/status` continues to show both as healthy with their queue_depth values exposed.

## [0.2.0] - 2026-05-04

### Added

- **Token-aware routing strategy (`token_aware`)** — picks the backend with the lowest predicted cost-weighted completion time. Score is `(backend.inflight_cost + request_cost) * (ewma_latency_ms or 1.0)`, where `request_cost = prompt_tokens * prefill_weight + max_tokens * decode_weight`. An unproven backend (no EWMA history) uses a neutral factor of `1.0` so it isn't trivially preferred. Ties are broken by inflight count, then backend name (deterministic).
- **Heuristic token estimator** (`meridian/router/token_estimator.py`) — counts ~4 chars/token plus per-message and per-request overhead; understands OpenAI multi-modal content blocks (counts text parts only); honors both `max_tokens` and `max_completion_tokens` request fields.
- **`Backend.inflight_cost`** — per-backend cost-weighted load tracking, incremented at request start, decremented in the finally block (works for both stream and non-stream paths). Surfaced in `/meridian/status` and on the `/ui` dashboard as a new "Cost Inflight" stat.
- **`GatewayConfig` knobs** — `prefill_weight` (default `1.0`), `decode_weight` (default `4.0`), `default_max_tokens` (default `256`), `token_estimator` (default `"heuristic"`). Only consulted when `strategy == "token_aware"`.

### Changed

- **`RoutingStrategy.select()`** now accepts an optional `request_ctx: RequestContext | None` parameter. Existing strategies (`weighted_round_robin`, `least_inflight`, `ewma_latency`) ignore it; only `token_aware` requires it. Backwards-compatible at every call site that didn't pass the new arg.

### Notes on empirical performance

The CLAUDE.md DoD for Milestone B asks for "improved p95 and reduced variance vs `least_inflight` on a mixed workload (50% `max_tokens=32`, 50% `max_tokens=2048`)." We ran that comparison against the bundled mock backends (50ms / 300ms fixed sleep, no concurrency cap) and found the two strategies converge on essentially identical p95/p99, with `token_aware` slightly higher on mean and stdev. This is **expected** — the mock backends do not model capacity (no queue depth, no batching window, no GPU contention), so per-request latency is independent of routing choice and there is nothing for cost-aware prediction to optimize. The strategy is implemented correctly; its differentiating behavior fires when backends have real capacity limits that make completion time depend on inflight load.

Validated:
- Unit + integration tests prove the algorithm fires when `inflight_cost` differs between candidates (selection flips deterministically).
- End-to-end smoke against a real Ollama backend (`qwen2.5:0.5b`) confirms the wiring (real chat, real stream, EWMA reflects real GPU latency, JSONL records `chosen_backend`).
- Empirical p95 / variance improvement over `least_inflight` requires capacity-bound backends; that signal will arrive with **Milestone C — telemetry adapters**.

## [0.1.1] - 2026-05-04

### Added

- **Docker image publishing** — multi-arch (`linux/amd64`, `linux/arm64`) build-and-push workflow at `.github/workflows/release.yml`. Triggers on `v*` tags and manual `workflow_dispatch`. Publishes to Docker Hub (`lothnic0801/meridian:<version>` + `:latest`) and GHCR mirror (`ghcr.io/<owner>/meridian`).
- **Smoke test script** — `scripts/smoke_test.py` exercises `/v1/models`, non-streaming chat, and streaming chat against a running gateway; asserts `x-request-id` and `x-meridian-backend` headers and `[DONE]` terminator on streams.
- **`.dockerignore`** — trims build context (excludes `.venv`, caches, tests, JSONL logs) so published images stay small and reproducible.
- **Quickstart docs** — README now documents `docker pull` + `docker run` alongside the existing Compose demo.

## [0.1.0] - 2026-03-05

### Added

- **OpenAI-compatible API** — `/v1/chat/completions` (streaming and non-streaming), `/v1/models`
- **3 routing strategies** — `weighted_round_robin`, `least_inflight`, `ewma_latency`
- **Health checking & automatic failover** — active endpoint pings with configurable thresholds, passive failure detection from request path
- **Streaming SSE passthrough** — zero-copy byte forwarding preserving event boundaries
- **Prometheus metrics** — request counters, latency histograms, inflight gauges, backend health gauges at `/metrics`
- **JSONL audit logs** — every request logged with metadata (request ID, backend, model, stream, latency, status); no prompts logged by default
- **Live operator dashboard** — real-time UI at `/ui` showing backend health, routing stats, and recent requests
- **Docker Compose demo** — one-command setup with mock backends for quick evaluation
- **YAML configuration** — gateway, health, logging, and backend settings in a single config file
- **Custom response headers** — `x-request-id` and `x-meridian-backend` on every proxied response
- **Example configs** — mock demo, single GPU (Ollama/vLLM), and dual-backend (failover testing) configurations
- **Delay proxy** — `delay_proxy.py` for single-GPU failover testing with configurable latency
