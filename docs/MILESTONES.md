# Meridian — Milestone history (what we built and why)

This is the **narrative record of product progress**: every major milestone, what
shipped, **why it mattered**, and how pieces fit together.

| Doc | Role |
|-----|------|
| **This file** | Full story: motivation + design intent per milestone |
| [`ship.md`](./ship.md) | One-line status table + “what’s next” |
| [`CHANGELOG.md`](../CHANGELOG.md) | Release-note detail per version |
| [`ROADMAP.md`](./ROADMAP.md) | Ordering of backlog (what’s left) |
| [`V1_ROADMAP.md`](./V1_ROADMAP.md) | Detailed plans / DoD for remaining work |
| [`PITCH.md`](./PITCH.md) | What sales can claim (must match **tagged** releases) |

_Last updated: 2026-07-10. Latest tagged: **v0.9.0**. Next: **0.9.1** product completion (v1.0 deferred)._

---

## Product arc in one paragraph

Meridian is an **L7 OpenAI-compatible inference gateway**: apps talk to one
endpoint; Meridian routes across self-hosted backends (vLLM, Ollama, etc.),
adds reliability and observability, then grew into **multi-tenant controls**,
**India-focused compliance (PII)**, **cost attribution**, and **deployable
packaging**—without becoming an inference engine and without requiring app
code changes.

Guiding rules (from day one):

1. **Reliability + visibility** for inference fleets  
2. **Backend-agnostic** (no deep engine hooks for core value)  
3. **Opt-in enterprise features** (defaults keep OSS/demo simple)  
4. **Never log prompts by default**; metrics avoid unbounded label cardinality  
5. **One milestone ≈ one branch ≈ one PR ≈ changelog entry**

Architecture (stable through all milestones):

```text
Client → Meridian (FastAPI)
           │  auth / policy / budgets / PII / cost
           │  route (strategy + tier + affinity)
           │  proxy (stream passthrough)
           ▼
        Backend 1..N
```

Post-refactor composition (before M/N landed on this shape):

| Module | Role |
|--------|------|
| `meridian/api/state.py` | `AppState` — runtime deps |
| `meridian/api/pipeline.py` | Parse body + policy chain |
| `meridian/api/routing.py` | Tiering + session affinity + strategy |
| `meridian/api/finalize.py` | Sync teardown (stream-safe) |
| `meridian/api/main.py` | Thin HTTP routes + lifespan |

---

## Core gateway — `v0.1.0`

### What
- OpenAI-compatible **`/v1/chat/completions`** (stream + non-stream) and **`/v1/models`**
- Streaming **SSE passthrough** (no full-response buffering)
- Routing strategies: weighted round-robin, least inflight, EWMA latency
- Active health checks + passive failure detection → **failover**
- Prometheus metrics, metadata-only JSONL request logs
- Operator UI (`/ui`), YAML config, Docker Compose demo with mock backends

### Why
Teams already run inference servers. What they lack is a **single production
edge**: one URL, health-aware routing, and operator visibility—without rewriting
clients or binding to one engine.

---

## Milestone A — Distribution — `v0.1.1`

### What
- Multi-arch container images (amd64/arm64), release CI (GHCR; Docker Hub optional)
- `scripts/smoke_test.py` for published images
- Docker pull / run quickstart docs

### Why
If install is hard, nothing else matters. **“docker pull + config”** is the
adoption funnel for OSS and the reproducibility baseline for enterprise PoCs.

---

## Milestone B — Token-aware routing — `v0.2.0`

### What
- Strategy **`token_aware`**: estimate prompt tokens + `max_tokens`, score
  `estimated_cost * latency_factor` (plus inflight cost on backends)
- Heuristic token estimator; gateway knobs (`prefill_weight`, `decode_weight`, …)

### Why
Not all requests are equal. Mixing short and long generations on “least
inflight” alone causes **head-of-line blocking**. Routing by estimated cost is
the first **inference-aware** win that stays backend-agnostic.

---

## Milestone C — Telemetry adapters — `v0.2.1`

### What
- `meridian/telemetry/`: JSON poll adapter for queue depth / GPU mem / tok-s
- Capacity penalties on `token_aware` (`queue_weight`, `mem_weight`)
- Status + UI surfaces for capacity signals
- Invariant: **telemetry tilts preference; health gates eligibility**

### Why
Gateway counters alone miss “healthy but overloaded.” Optional backend signals
shift traffic **before** hard failures—still without engine-specific code.

---

## Milestone D — Workload tiering — `v0.3.0`

### What
- Map request shape → tiers: `long_prompt` / `long_decode` / `default`
- Select backends by **tags**; fallback to all healthy if a pool is empty
- Headers/logs: `x-meridian-tier`

### Why
Production-friendly **prefill/decode-ish separation** without true
disaggregated serving: dedicate pools by request shape via config only.

---

## Milestone E — KV-affinity lite — `v0.3.1`

### What
- Optional session stickiness via `x-meridian-session` (default header)
- In-memory map, sliding TTL, max sessions, background sweep
- Remap if pinned backend is unhealthy; `x-meridian-session-route` =
  `new` / `pinned` / `remapped`

### Why
Continuations benefit from landing on the same backend (cache locality) **without**
reading engine KV internals. Reliability still wins over stickiness on failover.

---

## Side track — Tamper-evident audit pipeline

### What
Optional async path: Kafka/Redpanda → SHA-256 hash chain → Merkle tree →
Ed25519 signing → S3 Object Lock (WORM). Metadata-only events.

### Why
Regulated buyers ask “can we prove logs weren’t tampered with?” This answers
**audit integrity**, separate from day-to-day JSONL/Prometheus.

---

## Milestone F — API-key authentication — `v0.4.0`

### What
- Opt-in `auth.enabled`; Bearer keys `mrdn_…` on `/v1/*`
- Keys map to **org / team / user** identity
- OpenAI-shaped 401s; `/metrics`, `/meridian/*`, `/ui` left open for ops plane

### Why
Without identity, every later control (rate limits, budgets, cost, PII policy
overrides) has nothing to hang on. **Identity is the keystone.**

---

## Milestone G — Identity-aware logging — `v0.4.0`

### What
- Attach `org_id` / `team_id` to JSONL and audit events (never the raw key)

### Why
Attribution and incident response need “which tenant,” not “which IP behind the LB.”

---

## Milestone H — Per-org rate limiting — `v0.4.0`

### What
- When authed, rate-limit key = `org:{org_id}` (else `ip:…`)
- Token bucket; later **org overrides** live under `rate_limit.org_overrides`

### Why
IP rate limits are meaningless behind a VPC load balancer. Tenants share a
fair bucket regardless of source IP.

---

## Milestone I — Model access control — `v0.5.0`

### What
- Per-key `allowed_models`; disallowed model → **403** `permission_error`
- Empty list = unrestricted; only when auth is on
- Identity field: `allowed_models` (honest name; formerly overloaded `scopes`)

### Why
Not every key should call every model (cost, safety, tiering). Config-only
governance, no app changes.

---

## Milestone J — Tenant budgets & quotas — `v0.5.0`

### What
- `UsageMeter`: SQLite (default for durability) + in-memory
- Caps: tokens (via estimated `request_ctx.cost`) + requests; daily/monthly;
  org → team → user cascade
- Pre-flight `check_and_increment` → **429** + `Retry-After`
- Chat order: access → (later PII) → budget → rate limit → route  
  so 403/budget denials don’t spend rate tokens
- Metrics: `meridian_budget_rejections_total{level,period}` only

### Why
CFO / platform owners need **hard spend/shape caps** without parsing response
bodies (preserves streaming zero-copy). Estimates are conservative by design;
actuals come later (M).

---

## Milestone K — Hardening — `v0.6.0`

### What
- **Bounded rate-limit store** (idle TTL + max keys + eviction)
- **Stream cancel safety**: sync finalize; audit enqueue without await in
  stream `finally`; client disconnect → 499 / `client_disconnect`
- **Body size cap** → 413 (`gateway.max_body_bytes`)
- **Container**: non-root user, HEALTHCHECK, slim bookworm base
- Version consistency checks in CI / release

### Why
Before design-partner / enterprise load: no unbounded memory growth on RL maps,
no lost audit/inflight on disconnect, no multi-GB POST DoS, no root container
as default.

---

## Milestone L — PII detection (India pack) — `v0.7.0`

### What
- `meridian/pii/`: Aadhaar (**Verhoeff**), PAN, GSTIN, IFSC, UPI, Indian mobile
- Policies: `block` | `redact_and_replace` | `redact_for_logs` | `audit_only`
- Global + per-key `pii_policy`
- **Matched values never logged** — only type counts in JSONL/audit/metrics
- Request path only (response scanning deferred)

### Why
Sovereign / regulated India deployments need **prompt-side** DLP-ish controls
as a differentiator. Counts-only observability keeps the audit trail usable
without storing secrets.

---

## Architecture refactor (between L and M)

### What
- `AppState` instead of feature globals
- Policy **pipeline** (`prepare_chat_request`)
- Routing / finalize modules; thin `main.py`
- **Never forward** client Meridian `Authorization` upstream;
  optional `backends[].auth_header`
- Rate-limit org overrides moved off budgets onto `rate_limit.org_overrides`
- Ponytail cleanup: drop test shims and thin wrappers

### Why
Milestones had stacked policy `if`s into one god handler. Before cost
attribution and packaging, the composition layer had to stop growing spaghetti
so enterprise changes stay reviewable and safe.

---

## Milestone M — Cost attribution — `v0.8.0` (tagged)

### What
- Opt-in `cost:`: scrape **actual** `usage` from non-stream JSON and stream SSE
  tail (last 64KiB; **last usage wins**)
- Ledger: memory or **sqlite + WAL**; per-model prices per 1M tokens
- `GET /meridian/usage` + `/meridian/usage.csv`
- **Enterprise authz**: usage APIs require auth when cost is on; refuse open
  export if auth is off; non-admin **org/team scoped**; `cost_admin` for
  cross-org export
- Prometheus: `meridian_tokens_total{model,kind}` only (no org labels)
- Ops checklist: [`ENTERPRISE_COST.md`](./ENTERPRISE_COST.md)

### Why
Budgets answer “may this request run?” with **estimates**. Finance asks “what
did we actually spend?” M records backend-reported tokens and priced cost
without putting tenant IDs on high-cardinality metrics.

### Explicit non-goals (still true)
- Full multi-provider SSE dialects  
- Budget auto-reconcile / refunds on estimate vs actual  
- Org labels on Prometheus (use usage API instead)

---

## Milestone N — Deployment packaging — `v0.9.0` (tagged)

### What
- **Helm chart** `deploy/helm/meridian/` (Deployment, Service, ConfigMap, PVC,
  optional keys Secret, non-root security context)
- **Air-gap** `scripts/package_airgap.sh` + [`AIRGAP.md`](./AIRGAP.md)
- **Ops** [`DEPLOY.md`](./DEPLOY.md): TLS edge, backup/retention, sizing, rotation
- **Key hot-reload**: `auth.keys_file`; **SIGHUP** or `POST /meridian/reload`
  with `ops_admin` key; atomic `key_index` swap
- **`docs/MILESTONES.md`** narrative history

### Why
A 1-hour PoC / air-gapped bank install cannot depend on “clone and hope.”
Packaging + key rotation without full restarts are table stakes once real
users are on the gateway.

---

## After N — 0.9.x product completion (not v1.0 yet)

**v1.0 is deferred** until the product is complete for multi-tenant enterprise
use (including the ~1000-user class deployment). Continue patch/minor 0.9.x:

| Version | Intent |
|---------|--------|
| **0.9.1** | Enterprise template config, `/meridian/version`, hard `cost`↔`auth` gate, API/docs completeness, Helm render in CI |
| **0.9.2+** | Budget↔actual reconcile, load/overhead numbers, deeper e2e, remaining ops gaps |
| **v1.0** | Later: design-partner PoC evidence + pitch honesty gate only |

---

## Dependency picture (shipped)

```text
Core + A (run anywhere)
   → B,C (smarter routing)
   → D,E (pools + stickiness)
   → Audit pipeline (optional integrity)
   → F,G,H (identity)
   → I,J (governance)
   → K (safe to expose)
   → L (compliance differentiator)
   → refactor (composition)
   → M (finance actuals)
   → N (install & rotate keys)
   → v1.0 = PoC proof + pitch honesty, not a feature dump
```

---

## Version / tag map

| Tag | Milestones |
|-----|------------|
| `v0.1.0` | Core |
| `v0.1.1` | A |
| `v0.2.0` | B |
| `v0.2.1` | C |
| `v0.3.0` | D |
| `v0.3.1` | E |
| `v0.4.0` | F, G, H |
| `v0.5.0` | I, J (changelog; confirm tags in your registry) |
| `v0.6.0` | K |
| `v0.7.0` | L |
| `v0.8.0` | M |
| `v0.9.0` | N |
| `v0.9.1` | Product-complete polish (when tagged) |

Always prefer **git tags + CHANGELOG** over `pyproject` alone for “what is
in production.”

---

## How to keep this file honest

1. When a milestone merges and tags, add a short **What / Why** section here.  
2. Update the one-line row in [`ship.md`](./ship.md).  
3. Update [`PITCH.md`](./PITCH.md) only for **tagged** capabilities.  
4. If “why” changes (e.g. estimate vs actual), document it here—not only in code comments.
