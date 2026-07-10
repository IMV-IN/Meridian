# Road to v1.0 — Detailed Milestone Plan

This is the execution plan from today's state (Milestones A–I shipped, see
[`ship.md`](./ship.md)) to a **v1.0 launch as a startup**. It extends
[`ROADMAP.md`](./ROADMAP.md) (the strategic backlog) with per-milestone scope,
DoD, tests, and release mechanics. When they conflict, `ROADMAP.md`'s ordering
principles win; this file wins on milestone detail.

**v1.0 definition:** not a feature count. v1.0 = *one design-partner org has
completed the 4-week PoC (see [`PITCH.md`](./PITCH.md)) on a tagged release,
and every claim in the pitch deck is backed by shipped code.*

_Last updated: 2026-07-02._

---

## Guiding constraints (inherited from CLAUDE.md)

- Every feature must improve **reliability, operator control, or visibility**
  without app-code changes. Knobs that don't move one of those don't ship.
- One milestone = one branch = one PR = one tag = one release-note entry.
- Nothing lands without tests + docs + a reproducible validation recipe.
- Backend-agnostic, single-node features first; never block on multi-node or
  deep engine hooks.
- **Deliberately deferred past v1.0:** multi-provider routing, semantic
  caching, edge control plane, prefix-cache noncing, true KV-aware routing.
  Multi-provider before v1.0 means competing with LiteLLM/Portkey on their
  turf instead of owning the sovereign-compliance niche.

---

## Milestone J — Tenant budgets & quotas (`v0.5.x`)

**Status:** implemented (branch `milestone/J-tenant-budgets`). Spec:
`docs/superpowers/specs/2026-06-30-v0.5-tenant-governance-design.md`.

**Objective:** complete the identity keystone — org→team→user budget caps so
the CFO pitch ("track every rupee") is real.

Shipped
- Pluggable `UsageMeter`: `SqliteUsageMeter` (default) + `InMemoryUsageMeter`.
- Caps: tokens + requests, daily/monthly, org→team→user cascade.
- Pre-flight metering on `request_ctx.cost`. **0.9.2** reconciles token meters
  against upstream `usage` after success (stream tail / non-stream JSON).
- 429 `"rate_limit_exceeded"` + `Retry-After`; Prometheus
  `meridian_budget_rejections_total{level,period}`.
- Chat path order: access → budget → rate-limit → route (403/budget 429 do
  not spend rate tokens).
- Per-org rate-limit overrides on `budgets.orgs.<id>.token_capacity|refill`.

Deferred (not required for J DoD; partial later)
- Actual-token reconciliation → **shipped in 0.9.2**.
- `x-meridian-budget-remaining` response headers.

Compute: none (mock backends).

---

## Milestone K — Hardening release (`v0.6.0`)

**Status:** implemented (branch `milestone/K-hardening`).

**Objective:** fix the known correctness issues and make the gateway safe to
put in front of a design partner.

Shipped
1. **Bounded rate-limit store** — `RateLimitStore` idle TTL + max keys + sweep.
2. **Streaming cancellation safety** — sync `_finalize_request` + audit
   `enqueue()` (no await in stream `finally`); 499 on client disconnect.
3. **Container hardening** — non-root `USER 10001`, `HEALTHCHECK`,
   `python:3.11-slim-bookworm`.
4. **Release hygiene** — `pyproject` / `__version__` at **0.6.0**; CI checks
   package version consistency; release workflow checks tag == pyproject.
5. **Request body size cap** — `gateway.max_body_bytes` (default 10 MiB) → 413.

Deferred (manual / follow-up)
- Full 1M-request soak RSS report and published p50/p95 gateway overhead table.
- Historical git tags for v0.4.0/v0.5.0 (operator action after merge if desired).

Compute: none.

---

## Milestone L — PII detection & redaction (`v0.7.0`)

**Status:** shipped — tag **v0.7.0** (2026-07-09).

**Objective:** the compliance differentiator. India entity pack first.

Shipped
- `meridian/pii/`: regex-first detectors + Verhoeff for Aadhaar; PAN, GSTIN,
  IFSC, UPI, Indian mobile.
- Policies: `block` · `redact_and_replace` · `redact_for_logs` · `audit_only`
  (global + per-key `pii_policy`).
- Findings store **spans + redaction placeholder only** — never the raw match.
- JSONL/audit get `pii: {entity: count}`; metrics
  `meridian_pii_detections_total{entity,policy}`.
- Request-path only; response scanning deferred (documented in README/SECURITY).

Compute: none.

---

## Milestone M — Cost attribution (`v0.8.0`)

**Status:** next (not started). Depends on J's `UsageMeter` + identity.

**Objective:** the second half of the CFO pitch — turn J's metering into
reports.

Scope
- Capture actual `usage` (prompt/completion tokens) from backend responses,
  including streamed final chunks, into the `UsageMeter`.
- Per-model price table in config (₹ or $ per 1M prompt/completion tokens).
- New endpoints: `/meridian/usage?org=&team=&window=` (JSON) and matching
  Prometheus counters (`meridian_tokens_total{org,team,model,kind}` — labels
  bounded by config).
- CSV export for finance (`/meridian/usage.csv`).

DoD
- Run mixed traffic for 2 orgs → per-team token + cost report matches backend
  ground truth within 1%.
- Cardinality audit: label sets remain bounded by configured keys/models.

Tests
- Unit: streamed-usage extraction, price math, window aggregation.
- Integration: two-org traffic → report correctness.

Compute: none (mock usage payloads); Ollama for a real-usage sanity pass.

---

## Milestone N — Deployment packaging (`v0.9.0`)

**Objective:** a Week-1 PoC deploy must take under one hour of client time.

Scope
- **Helm chart** (`deploy/helm/meridian/`): gateway + optional audit stack
  (Redpanda, consumer, MinIO), values-driven config, secrets from k8s Secrets.
- **Air-gapped bundle**: image tarballs + compose profile + offline install
  doc; no external pulls.
- **Key management v1**: keys loadable from a separate secrets file; SIGHUP
  (or `/meridian/reload` admin endpoint) reloads keys without restart. Keys in
  `config.yaml` remain supported.
- **Ops docs**: sizing guide (from Milestone K's load numbers), TLS-fronting
  examples (nginx/traefik snippets), backup/retention guidance for SQLite
  meter + JSONL logs.

DoD
- `helm install` on a clean kind/k3s cluster → smoke test passes.
- Air-gapped install validated on a machine with no internet.
- Key rotation without dropping in-flight requests.

Tests
- CI job: helm template lint + kind-based install smoke test.
- Unit: hot-reload key index swap (no partial state visible to requests).

Compute: none.

---

## v1.0 gate (`v1.0.0`)

Ship nothing new. v1.0 is a verification milestone:

- [ ] One design-partner PoC completed on a tagged release (4-week plan in
      [`PITCH.md`](./PITCH.md)), with a written reference or case study.
- [ ] Every feature named in [`PITCH.md`](./PITCH.md) and the README exists in
      the tagged image — no "coming soon" items in the pitch.
- [ ] `pyproject.toml` version == git tag == Docker tag, enforced by CI.
- [ ] SECURITY.md checklist validated on the pilot deployment.
- [ ] Image scan clean (no HIGH/CRITICAL), runs non-root.
- [ ] Load numbers published in README (overhead vs direct backend).
- [ ] CHANGELOG + ship.md + ROADMAP.md reconciled.

---

## Timeline sketch (aggressive but serial, one person)

| Milestone | Est. effort | Cumulative |
|---|---|---|
| J — budgets/quotas | 2–3 wks | 3 wks |
| K — hardening | 1–2 wks | 5 wks |
| L — PII (regex pack) | 2–3 wks | 8 wks |
| M — cost attribution | 1–2 wks | 10 wks |
| N — packaging | 2 wks | 12 wks |
| Design-partner PoC (parallel to N where possible) | 4 wks | ~14–16 wks |

Start pilot outreach during **L** — the PoC needs a signed-up partner by the
time N ships, and enterprise sales cycles are longer than build cycles.

---

## Explicitly post-v1.0 (do not start before the gate)

See [`FEATURES.md`](./FEATURES.md) for detail. Headlines: multi-provider
routing, semantic caching, RBAC beyond org→team→user, NER-based PII, edge
control plane, prefix-cache noncing, KV-aware routing, fine-tuning pipelines.
