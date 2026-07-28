# Meridian — Full Gap & Roadmap

_Synthesized from subagent research on features, deployment, code health, and platform gaps vs Tensormux. Last updated: 2026-07-24._

---

## Executive summary

Meridian is **feature-complete as an open-source gateway** and has 25+ capabilities that Tensormux's OSS gateway lacks (multi-tenant auth, budgets, PII, cost attribution, Helm, air-gap, RBAC). Tensormux is effectively Meridian's v0.1.x core with no materially newer gateway features.

The real gaps are **not in the gateway itself** — they are in three areas:

1. **Code health** (test coverage, resource leaks, dependency hygiene)
2. **Platform capabilities** (autoscaling, dynamic config reload, circuit breaker, traffic management)
3. **Managed platform** (tenant isolation, billing, key lifecycle API, Grafana dashboards)

This roadmap addresses all three.

---

## Current state at a glance

| Dimension | Status |
|-----------|--------|
| Lint & types | ✅ Clean (ruff, mypy) |
| Tests | ✅ 357/357 pass |
| v0.11.0 | In tree (Phase 0–2 landed; Phase 3 platform depth next) |
| v1.0 | Blocked on cofounder/partner sign-off |
| Docker image | Multi-arch, hardened, non-root |
| Helm chart | Basic (137 lines, 8 templates) |
| Docs | Comprehensive (DEPLOY, OPS_RUNBOOK, AIRGAP, CONFIGURATION) |
| CI/CD | Robust with retries + version validation |
| **Test coverage** | ✅ 87% on `meridian/` (406 tests, 2026-07-28) |
| **Resource leaks** | ✅ Fixed (0.10.0) |
| **Dependency hygiene** | ✅ Optional `[audit]` extra (0.10.0) |
| **Circuit breaker / retry** | ✅ 0.10.0 (`resilience.circuit_breaker`, `max_retries` + backoff) |
| **Dynamic config reload** | ✅ 0.10.0 — full atomic reload via `POST /meridian/reload` / SIGHUP |
| **Autoscaling** | ✅ 0.11.0 scaffolding (KEDA ScaledObject on inflight; HPA doc) |
| **Scale-to-zero** | ✅ 0.11.0 (`idle_timeout_min` + `meridian_backend_idle`; KEDA wiring in DEPLOY) |
| **Traffic management** | ❌ No canary / blue-green / gradual rollout |
| **Tenant isolation** | ❌ All tenants share same process / backend pool |
| **Billing** | ❌ Cost tracking yes, no billing integration |
| **Key lifecycle API** | ❌ Only config-file + SIGHUP |
| **Per-key usage tracking** | ❌ Rate limiting and budgets are org-scoped only |
| **Grafana dashboards** | ✅ 0.11.0 (`deploy/grafana/` + Helm ConfigMap) |
| **K8s Ingress / TLS / PDB** | ✅ 0.11.0 (+ ServiceAccount, topology spread) |

---

## Priority-ranked issues

### P0 — Must land before v1.0 (per the "complete product" strategy)

| # | Issue | Impact | Status |
|---|-------|--------|--------|
| P0-1 | No circuit breaker or retry on upstream failures | Transient backend blips → immediate 502s to clients | ✅ **0.10.0** |
| P0-2 | No dynamic config reload (auth keys only) | Strategy, backend, and scaling rule changes require full restart | ✅ **0.10.0** |
| P0-3 | Open file handle in `metrics/logger.py:18` | File descriptor leak; unacceptable in a "finished" product | ✅ **0.10.0** |
| P0-4 | ~25 modules with zero test coverage | A complete product tag cannot ship with untested core paths | ✅ **87% cov** |
| P0-5 | Audit deps (`aiokafka`, `boto3`) hard-required | Bloated install surface; trivial fix | ✅ **0.10.0** |

_(With the new sequencing, partner sign-off is no longer a "blocker" — it's the final verification step in Phase 4.)_

### P1 — Maturity gaps (land in Phase 2 / Phase 3)

| # | Issue | Impact |
|---|-------|--------|
| P1-1 | No Grafana dashboard templates | Users can't observe routing behavior out of the box |
| P1-2 | No K8s Ingress / TLS / PodDisruptionBudget / TopologySpreadConstraints in Helm | Forces users to build their own K8s wiring; poor operator DX |
| P1-3 | No traffic management (canary, blue/green, weighted shifts) | Cannot do safe rollouts of backend changes |
| P1-4 | No autoscaling or scale-to-zero | Wasted GPU resources during idle; production cost concern |

### P2 — Platform capability gaps

| # | Issue | Impact |
|---|-------|--------|
| P2-1 | No tenant runtime isolation | Noisy neighbor can exhaust backend capacity for all tenants |
| P2-2 | No billing integration | Cost tracking exists but operators must build their own billing pipeline |
| P2-3 | No key lifecycle API (create/delete/rotate via API) | Key management requires config file edits + SIGHUP |
| P2-4 | No per-key usage tracking | Only org-scoped usage; cannot enforce per-key quotas |
| P2-5 | No global or per-model rate limiting | Only per-org rate limiting exists |
| P2-6 | No scale-to-zero support | Idle backends consume GPU resources indefinitely |

### P3 — Nice-to-have

| # | Issue | Impact |
|---|-------|--------|
| P3-1 | No multi-region / edge support | Future; requires multi-node infra |
| P3-2 | No semantic caching | Deferred per roadmap |
| P3-3 | No batch inference endpoint | Deferred per roadmap |
| P3-4 | No prefix-cache noncing | Deferred; requires deep engine hooks |
| P3-5 | No KV-cache-aware routing | Deferred; requires multi-node + engine signals |

---

## Roadmap — v1.0 as the complete product

> **Strategy change (2026-07-24):** v1.0 is no longer a "verified baseline" tag with features added after. v1.0 ships **feature-complete**: Phase 0 code health + Phase 1 reliability + Phase 2 observability/ops + Phase 3 platform depth all land **before** the tag. The v1.0 gate (partner sign-off, honesty check) runs **last** as the final seal, not as a blocker that postpones features.

This means the pre-v1.0 track becomes `v0.10.0` … `v0.13.0` (one milestone per phase, in the project's established cadence), and `v1.0.0` caps the series.

---

### Phase 0 — Code health & reliability (foundation; `v0.10.0`)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Resource leaks** | Fix open file handle in `metrics/logger.py` — use context manager or `__aenter__`/`__aexit__` | `RequestLogger.close()` always called; no open fd after shutdown |
| **Dependency hygiene** | Make `aiokafka` and `boto3` optional extras (`[audit]`) in `pyproject.toml` | `pip install meridian` works without them; `pip install meridian[audit]` includes them |
| **Test coverage** | Add tests for all untested modules (see table below) | ≥ 80% coverage on `meridian/` excluding `ui/` static assets |
| **Type consistency** | Align `mypy python_version` with `requires-python` (≥3.9) | `mypy meridian` passes cleanly |

Untested modules to cover:

| Module | Lines | Test approach |
|--------|-------|---------------|
| `meridian/proxy/forward.py` | 108 | Mock backend + httpx responses; test stream/non-stream/error paths |
| `meridian/health/checker.py` | 64 | Mock backend responses; test active + passive detection |
| `meridian/metrics/collectors.py` | 58 | Verify all metrics are registered and updated correctly |
| `meridian/cost/ledger.py` | 227 | Test daily/monthly cost aggregation, CSV export, authz gating |
| `meridian/cost/extract.py` | 73 | Test usage extraction from non-stream JSON and SSE tail |
| `meridian/cost/record.py` | 45 | Test cost record creation and schema |
| `meridian/pii/detectors.py` | 190 | Test each detector (Aadhaar, PAN, GSTIN, IFSC, UPI, mobile) |
| `meridian/pii/policy.py` | 64 | Test each policy (block, redact_and_replace, redact_for_logs, audit_only) |
| `meridian/pii/scan.py` | 106 | Test request-path scanning + redaction |
| `meridian/pii/types.py` | 74 | Test PII entity types and matching |
| `meridian/audit/publisher.py` | 177 | Test Kafka + Redpanda publisher paths |
| `meridian/audit/archiver.py` | 87 | Test S3 WORM + hash chain integration |
| `meridian/audit/hash_chain.py` | 86 | Test hash chain integrity |
| `meridian/telemetry/base.py` | 36 | Test TelemetryAdapter ABC contracts |
| `meridian/usage/sqlite.py` | 149 | Test SQLite usage meter CRUD + TTL |
| `meridian/api/errors.py` | 27 | Test all error response shapes |
| `meridian/api/finalize.py` | 124 | Test stream/non-stream teardown paths |
| `meridian/api/state.py` | 212 | Test AppState lifecycle and component wiring |

### Phase 1 — Resilience & dynamic ops (`v0.10.0` continued)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Circuit breaker** | Per-backend circuit breaker: open after N consecutive failures, half-open probe, half-open → closed/reset | `forward.py` returns 503 when circuit is open; probes at configurable interval; circuit state visible in `/meridian/status` |
| **Retry with backoff** | Configurable retry on upstream `httpx.RequestError` with exponential backoff | `max_retries`, `retry_backoff_base` in config; retried requests counted in a new Prometheus counter |
| **Request timeout config** | Make connect/read/write/pool timeouts configurable per-backend and globally | `timeout.{connect,read,write,pool}` in `BackendConfig`; defaults preserve current behavior |
| **Dynamic config reload** | Full config reload via `POST /meridian/reload` + SIGHUP (not just auth keys) | Changing strategy, backends, weights, or tiering takes effect without restart; reload is atomic (bad config → reject + keep old) |
| **Configurable stream timeouts** | `timeout.stream_read` config for long-running SSE connections | Stream closed gracefully after timeout; client gets a well-formed SSE end |

### Phase 2 — Observability, packaging & elasticity (`v0.11.0`)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Grafana dashboards** | Ship `deploy/grafana/` with pre-built dashboards: routing decisions, backend health, budget/PII events, cost overview | Dashboards importable as JSON; Helm optional `grafana.enabled` deploys them via ConfigMap |
| **Helm hardening** | Add `PodDisruptionBudget`, `TopologySpreadConstraints`, `RBAC`/`ServiceAccount`, optional `Ingress` + TLS to chart | `helm template` validates in CI; docs show ingress + cert-manager example |
| **Autoscaling scaffolding** | KEDA `ScaledObject` template (on queue_depth or Prometheus inflight metric) + HPA example | KEDA config in `values.yaml` (off by default); `docs/DEPLOY.md` gets an autoscaling section |
| **Scale-to-zero** | Idle-timeout marking per backend: `backends[].idle_timeout_min`; gateway stops routing to idle backends and exports a `meridian_backend_idle` gauge that external scalers (KEDA) can drive replicas off | Backend with no traffic for N min → marked `idle`; first request re-marks healthy; docs explain KEDA scale-to-zero wiring |

### Phase 3 — Platform depth & multi-tenancy (`v0.12.0`)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Tenant isolation modes** | `deployment_mode: shared | dedicated`; in dedicated mode, orgs can be pinned to backend tag pools | Shared = today's behavior; dedicated = org→pool mapping in config; noisy-org traffic cannot starve another org's pool |
| **Traffic management** | Canary: `routing.canary` — weight shift schedule with health-based promotion/demotion, auto-rollback on error-rate spike | Weight drift observable in metrics; canary state in `/meridian/status`; rollback tested in e2e |
| **Key lifecycle API** | `POST /meridian/keys`, `DELETE /meridian/keys/{id}`, `GET /meridian/keys` (ops_admin-only) | Keys manageable without config edits; everything still syncs to `keys_file` for durabiity |
| **Per-key usage tracking** | Usage counters keyed by (key, org) not just org | `/meridian/usage` supports `?key=…` filter; per-key export in CSV |
| **Per-model + global rate limiting** | `rate_limit.models.<model>` and `rate_limit.global` blocks | 429 + `Retry-After` on breach; metrics `meridian_ratelimit_rejections_total{scope=model|global|org}` |

### Phase 3.5 — Optional revenue track (`v0.13.0`, can ship post-v1.0 if scope pressure)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Billing adapter interface** | Pluggable `BillingAdapter` (webhook/event push of usage records); reference: Stripe usage-records connector | `billing.enabled`; adapter pushes per-period usage; failure mode = log + retry, never block requests |

> ⚠️ Mission check: billing is the least "reliability + visibility" item here. If any item gets cut for scope, this is it — the usage ledger already gives finance what it needs to bill out-of-band.

### Phase 4 — Tag v1.0.0 (the seal, not the feature drop)

| Track | Deliverable | Acceptance criteria |
|-------|-------------|---------------------|
| **Partner PoC** | Design-partner runs the complete product (Phases 0–3 tagged as `v0.13.x` RC) for 4 weeks | `docs/internal/POC_REPORT.md` updated with RC evidence + sign-off |
| **Pitch sync** | `PITCH.md` claims now cover resilience, autoscaling, traffic management, isolation — all proven on the RC tag | Every claim tested on the RC |
| **Tag v1.0.0** | Bump version, tag, publish `meridian/meridian:1.0.0` | GitHub + GHCR + Docker Hub, release notes with validation recipe |
| **Security checklist** | Operator runbook includes security checklist (TLS, auth, network isolation, backup) | `docs/DEPLOY.md` explicit checklist section |

### Phase 5 — Advanced & deferred (post-v1.0)

| Track | Deliverable | Blocker |
|-------|-------------|---------|
| **Multi-region edge** | Deploy Meridian to edge PoPs (Cloudflare Workers, AWS CloudFront Functions) | Multi-node infra + global load balancing |
| **Semantic caching** | Cache semantic embeddings of prompts; return cached response for near-duplicate requests | Embedding model integration; cache invalidation strategy |
| **Batch inference** | Async bulk endpoint for batch completions | Backend support for batch mode |
| **Prefix-cache noncing** | Prevent cache poisoning in multi-tenant vLLM deployments | Deep vLLM multi-tenant hooks |
| **KV-cache-aware routing** | Route to backends with warm KV caches for continuation requests | Engine-internal KV residency signals |
| **True prefill/decode disaggregation** | Separate prefill and decode pools with dynamic work-stealing | Engine-level scheduler integration |

---

## Phase 0 immediate actions (this week)

```bash
# 1. Fix resource leak in metrics/logger.py
# Replace open() with context-manager pattern or ensure close() is always called

# 2. Make audit deps optional
# Edit pyproject.toml: move aiokafka/boto3 to optional [audit] extra

# 3. Add tests for untested modules
# Start with proxy/forward.py and health/checker.py (highest impact)

# 4. Run coverage audit
# pytest --cov=meridian --cov-report=term-missing
```

---

## Dependency summary

```
Phase 0 (code health v0.10.0)
        │
Phase 1 (resilience + dynamic config v0.10.0)
        │
Phase 2 (observability + elasticity v0.11.0)
        │
Phase 3 (platform depth v0.12.0) ── (optional) Phase 3.5 (billing v0.13.0)
        │
Phase 4 (partner RC PoC → tag v1.0.0)
        │
Phase 5 (deferred advanced — design-partner driven only)
```

One milestone = one branch = one PR = one tag, as established. The sign-off moves from a **blocker** to the **final checkpoint**: features land first under honest 0.x tags, the partner validates the whole thing, and v1.0.0 is the stamp on a product that already exists.

---

## What this roadmap is not

- **Not a feature wish list.** Every item must improve reliability, operator control, or visibility (billing is the flagged exception — cuttable for scope).
- **Not a v1.0 feature dump.** The v1.0 gate still runs — it runs **after** features, verifying an RC that a partner actually loaded.
- **Not a replacement for ROADMAP.md or MILESTONES.md.** Those files control ordering; this file documents gaps and the plan to close them. If this plan is adopted, `V1_GATE.md` and `ROADMAP.md` should be updated to match (features first, sign-off last).