# Meridian — Ship Log

A scannable record of **what's shipped** and **what's next**. For the full
strategic picture see [`ROADMAP.md`](./ROADMAP.md); for per-change detail see
[`../CHANGELOG.md`](../CHANGELOG.md). Keep this file updated as milestones land.

_Last updated: 2026-07-09 (Milestone K)._

---

## Shipped

| Milestone | Version | What |
|---|---|---|
| Core gateway | `v0.1.0` | OpenAI-compatible API, SSE streaming passthrough, health checks + automatic failover, Prometheus metrics, metadata-only JSONL logs, operator UI |
| **A** — Distribution | `v0.1.1` | Multi-arch Docker images, smoke test, release CI (GHCR always; Docker Hub when configured) |
| **B** — Token-aware routing | `v0.2.0` | Route by estimated request cost (prefill/decode weights, EWMA latency) |
| **C** — Telemetry adapters | `v0.2.1` | Capacity-aware routing inputs (generic JSON poll adapter) + queue/mem penalties |
| **D** — Workload tiering | `v0.3.0` | Dedicated backend pools by request shape (`long_prompt` / `long_decode` / `default`), tag-driven, fallback to all-healthy |
| **E** — KV-affinity lite | `v0.3.1` | Session stickiness via `x-meridian-session`, sliding-TTL in-memory store, remap on unhealthy |
| **F** — API-key auth | `v0.4.0` | Opt-in Bearer-key gate on `/v1/*`, config-driven, disabled by default; keys map to org/team/user identity |
| **G** — Identity-aware logging | `v0.4.0` | `org_id`/`team_id` attached to JSONL logs + audit events (metadata only; key never logged) |
| **H** — Per-identity rate limiting | `v0.4.0` | Token bucket keys on `org:{org_id}` when authenticated (else `ip:{ip}`); same org shares a bucket across source IPs |
| **I** — Model access control | `v0.5.0` | Per-key `allowed_models` allow-list; disallowed model → 403 (`permission_error`); empty list = unrestricted |
| **J** — Tenant budgets & quotas | `v0.5.0` | Pluggable `UsageMeter` (SQLite default + in-memory), org→team→user caps (tokens + requests, daily/monthly), pre-flight metering → 429, per-org rate-limit overrides, `meridian_budget_rejections_total` |
| **K** — Hardening | `v0.6.0` | Bounded rate-limit store (TTL + max keys), stream-cancel-safe finalize, body size cap 413, non-root Docker + HEALTHCHECK, version consistency CI |

Also shipped outside the A–E track: IP-based rate limiting (token bucket) and
the tamper-evident audit pipeline (Kafka → SHA-256 hash chain → Merkle →
Ed25519 → S3 Object Lock WORM).

Identity keystone (F–J) + hardening (K) complete. Next: PII (L), cost
attribution (M) — see [`ROADMAP.md`](./ROADMAP.md) / [`V1_ROADMAP.md`](./V1_ROADMAP.md).

---

## Next up

| Milestone | Status | What |
|---|---|---|
| **L** — PII detection & redaction | planned | India entity pack (Aadhaar/PAN/…), policies block/redact/audit. See `V1_ROADMAP.md`. |
