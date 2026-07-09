# Meridian Roadmap

Single source of truth for **ordering**. Per-milestone detail lives in
[`V1_ROADMAP.md`](./V1_ROADMAP.md); what has shipped is in
[`ship.md`](./ship.md). When these conflict on ordering, this file wins.

_Last reconciled: 2026-07-09._

---

## Shipped

| Item | Notes |
|---|---|
| Core gateway | OpenAI API, SSE streaming, health/failover, metrics, UI — `v0.1.0` |
| **A** — Docker images + smoke + release CI | `v0.1.1` |
| **B** — Token-aware routing | `v0.2.0` |
| **C** — Telemetry adapters + capacity penalties | `v0.2.1` |
| **D** — Workload tiering | `v0.3.0` |
| **E** — KV-affinity lite (session stickiness) | `v0.3.1` |
| Rate limiting (token bucket) | IP-based; upgraded by H |
| Tamper-evident audit pipeline | Kafka → hash chain → Merkle → Ed25519 → S3 WORM |
| **F** — API-key auth | `v0.4.0` |
| **G** — Identity-aware logging | `v0.4.0` |
| **H** — Per-org rate limiting | `v0.4.0` |
| **I** — Model access control | `v0.5.0` |
| **J** — Tenant budgets & quotas | `v0.5.0` |
| **K** — Hardening | `v0.6.0` (tagged) |
| **L** — PII detection & redaction | `v0.7.0` (unreleased) |

Identity keystone **F–J**, hardening **K**, and PII **L** are complete (tag L after merge).

---

## Ordered backlog

Ordering principle: **ship backend-agnostic, single-node features first; do not
block OSS progress on multi-node or deep-engine work.**

### Phase 1 — Cost attribution (next)

| Item | Depends on |
|---|---|
| **M — Cost attribution** — reconcile estimate vs actual `usage`, per-team reports | J (meter interface) |
| **Multi-provider routing** — OpenAI/Anthropic/Google + self-hosted | provider adapter layer |

### Phase 3 — Data-plane (mostly independent)

| Item | Source |
|---|---|
| **Semantic caching** | README |
| **Batch inference** — async bulk endpoint | README |

### Phase 4 — Packaging

| Item |
|---|
| **On-prem / air-gapped** — OCI + Helm, offline license keys |

### Phase 5 — Advanced (deferred — compute / engine hooks)

| Item | Blocker |
|---|---|
| Edge control plane (Workers PoPs) | multi-region infra |
| Prefix-cache noncing | deep vLLM multi-tenant |
| True KV-cache-aware / prefill-decode disaggregation | multi-node + engine signals |

---

## Dependency summary

```
A–E + audit (done)
      │
  F–J identity keystone (done)
      │
  K hardening (done)
      │
  L PII (done, tag pending)
      │
  M cost attribution  →  multi-provider
  semantic cache · batch  (independent)
  On-prem packaging
  Edge / prefix-cache / KV-aware  (deferred)
```

## Notes

- **Mission gate:** every item must improve reliability, operator control, or
  visibility without application code changes.
- **Shipping cadence:** one milestone = one branch = one PR = release-note entry.
  Tests + docs + validation recipe required before merge.
