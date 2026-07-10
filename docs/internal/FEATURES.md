# Additional Features — Post-v1.0 Candidates

Everything here is **deliberately not on the v1.0 path**
([`V1_ROADMAP.md`](./V1_ROADMAP.md)). Each entry records why it's deferred and
what would pull it forward, so future prioritization doesn't restart from
zero. Ordering within tiers ≈ expected pull-forward pressure from customers.

Mission gate still applies: a feature ships only if it improves reliability,
operator control, or visibility without app-code changes.

---

## Tier 1 — Likely first after v1.0 (customer-pull expected)

### Multi-provider routing (OpenAI / Anthropic / Google + self-hosted)
One endpoint fronting both sovereign backends and public APIs, with per-key
policy on which providers a tenant may reach.
- **Why deferred:** it drags Meridian into LiteLLM/Portkey's home turf before
  the sovereign niche is owned; provider adapters (auth, error shapes, usage
  formats) are a large surface.
- **Pull-forward trigger:** a paying pilot that needs "sovereign for regulated
  workloads, public API for the rest" through one gateway. This is a common
  hybrid ask — expect it early.
- **Depends on:** cost attribution (M) for per-provider pricing; PII policies
  (L) become *more* valuable here (block PII from leaving for public APIs —
  that's the sovereign framing of multi-provider, and the right way to build it).

### RBAC beyond org→team→user
Departments, per-level policy overrides, admin API for key CRUD (today keys
are config-file-only), key expiry/rotation schedules.
- **Why deferred:** J's hierarchy covers pilot needs; an admin API implies a
  persistence + authz layer that shouldn't be designed before real tenant
  feedback.
- **Pull-forward trigger:** first customer with >20 teams or a security
  review demanding key rotation SLAs.

### NER-based PII detection
ML-based entity detection (names, addresses) on top of L's regex pack;
response-side (completion) scanning.
- **Why deferred:** latency + model-hosting cost; regex pack covers the
  checksummed Indian identifiers that regulators actually enumerate.
- **Pull-forward trigger:** DPDP audit feedback that free-text PII must be
  caught; a customer in insurance/health where narratives carry PII.

## Tier 2 — Valuable, needs design investment

### Semantic caching
Cache completions for similar prompts at the gateway (embedding + vector
store), per-tenant isolation mandatory.
- **Why deferred:** correctness risk (stale/wrong answers) is a brand risk for
  a compliance product; needs an embedding dependency and invalidation story.
- **Pull-forward trigger:** a customer whose GPU bill is dominated by
  near-duplicate prompts (support bots, RAG boilerplate).

### Batch inference endpoint
Async `/v1/batches`-style bulk API: submit a file/list, poll for results.
- **Why deferred:** needs a job store + worker loop — first real stateful
  subsystem beyond the SQLite meter.
- **Pull-forward trigger:** back-office workloads (document processing,
  nightly scoring) at a pilot bank.

### Gateway high availability
Shared state (health, sessions, budgets) across gateway replicas — Redis or
gossip — so two Meridians behind an LB behave as one.
- **Why deferred:** single-gateway is honest for the target scale (50–150
  in-flight, §1.2 of [`ENTERPRISE_PROPOSAL.md`](./ENTERPRISE_PROPOSAL.md));
  budgets-in-SQLite is the first thing that breaks with 2 replicas.
- **Pull-forward trigger:** first customer with a hard HA requirement in
  procurement.

### Admin/control API
`/meridian/admin/*`: key CRUD, budget edits, backend drain/undrain at runtime
(today: config file + restart, hot key reload arriving in N).
- **Why deferred:** every admin surface is attack surface; config-as-file is
  auditable and fits GitOps.
- **Pull-forward trigger:** ops teams asking to drain a backend for
  maintenance without a config push.

## Tier 3 — Strategic bets (compute or deep-engine blocked, per CLAUDE.md §4)

### Edge control plane (Cloudflare Workers PoPs)
Auth + rate limiting at Mumbai/Chennai/Delhi PoPs; gateway stays data-blind.
- **Blocked on:** multi-region infra, and a customer whose latency/topology
  actually needs it.

### Prefix-cache noncing (per-tenant KV isolation)
Tenant nonces prepended to system prompts on shared vLLM nodes to prevent
cross-tenant KV timing side-channels.
- **Blocked on:** deep vLLM internals, multi-tenant GPU test bed. Research
  question §9.1 of the enterprise proposal.

### True KV-cache-aware routing / prefill-decode disaggregation
Route on engine cache-residency signals; dedicated prefill vs decode pools
across nodes.
- **Blocked on:** multi-node cluster, engine-exposed signals. Workload
  tiering (D) is the shipped 80% version.

### Fine-tuning pipelines
Containerized fine-tune jobs on customer data as a paid add-on.
- **Blocked on:** demand validation; year-2 item in the proposal.

## Tier 4 — Small quality-of-life (batch into any milestone, no dedicated release)

- `Retry-After` header on 429s.
- Per-backend connect/read timeout overrides in config.
- Structured (JSON) app logs option for `meridian.*` loggers.
- `/meridian/requests` filter params (`?org=`, `?backend=`, `?status=`).
- Anthropic-style `/v1/messages` compatibility shim (only if a pilot asks).
- OpenTelemetry trace export (spans per request → backend), gated behind
  config; complements Prometheus rather than replacing it.
- Health-probe auth header support (see
  [`HEALTH_CHECKS.md`](./HEALTH_CHECKS.md) limitations).
