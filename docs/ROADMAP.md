# Meridian Roadmap

Single source of truth for **ordering**. Narrative history (what + why) lives in
[`MILESTONES.md`](./MILESTONES.md); one-line status in [`ship.md`](./ship.md);
remaining plans in [`V1_ROADMAP.md`](./internal/V1_ROADMAP.md). When ordering conflicts,
this file wins.

_Last reconciled: 2026-07-28 (v0.10.0 in tree: Phase 0 code health + Phase 1 resilience; see [`FULL.md`](./FULL.md))._

---

## Shipped

Through **v0.8.0** (see [`MILESTONES.md`](./MILESTONES.md) for detail):

| Item | Tag |
|---|---|
| Core + A–E (gateway, routing, tiering, affinity) | `v0.1`–`v0.3.x` |
| Audit pipeline (optional WORM path) | alongside core track |
| F–J identity + governance | `v0.4`–`v0.5` |
| K hardening | `v0.6.0` |
| L India PII | `v0.7.0` |
| AppState/pipeline refactor | pre-M on main |
| M cost attribution (+ enterprise usage authz) | `v0.8.0` |
| N packaging (Helm, air-gap, key reload) | `v0.9.0` when tagged |

---

## Ordered backlog

Ordering principle: **ship backend-agnostic, single-node features first; do not
block OSS progress on multi-node or deep-engine work.**

### Phase 1 — Complete the 0.9.x product (**shipped**)

| Item | Notes |
|---|---|
| **0.9.1** | **shipped** — enterprise template, `/meridian/version`, cost requires auth at boot, Helm CI |
| **0.9.2** | **shipped** — budget↔actual token-meter reconcile |
| **0.9.3** | **shipped** — load harness, enterprise e2e, ops polish |
| **0.9.4** | **in tree** — RBAC roles, ops-endpoint auth, dashboard overhaul |
| **Ollama real-path proof** | **done** — `LOAD.md` section (~2 ms gateway overhead on qwen2.5:0.5b) |

### Phase 2 — Complete-product track `v0.10`–`v0.13` (**before v1.0**)

Per the 2026-07-24 sequencing change, **v1.0 ships feature-complete**. Detail,
per-track DoD, and acceptance criteria live in [`FULL.md`](./FULL.md). Summary:

| Tag | Theme | Key items |
|---|---|---|
| `v0.10.0` | Code health + resilience (**in tree**) | ~~Test coverage for untested core paths; optional `[audit]` deps; logger fd fix~~; circuit breaker; retry with backoff; per-backend timeouts; full dynamic config reload; stream timeouts |
| `v0.11.0` | Observability + elasticity (**next**) | Grafana dashboards; Helm PDB/Ingress/TLS/RBAC; KEDA scaffolding; scale-to-zero idle signal |
| `v0.12.0` | Platform depth | Tenant isolation modes; canary rollout; key lifecycle API; per-key usage; per-model + global rate limits |
| `v0.13.0` | Optional revenue track | Pluggable billing adapter (cuttable for scope) |

### Phase 3 — v1.0 seal (**runs last**)

| Item | Status |
|------|--------|
| Phase 2 tags shipped + green CI on each | **Open** |
| RC partner PoC (complete product, 4 weeks) | **Open** |
| Pitch / SECURITY / image scan re-verified on RC | **Open** |
| Partner / cofounder sign-off on RC | **Open** |
| Tag **v1.0.0** | **Hold** — see [`internal/V1_GATE.md`](./internal/V1_GATE.md) |

### Phase 4 — Data-plane (mostly independent, post-v1.0)

| Item | Source |
|---|---|
| **Semantic caching** | README |
| **Batch inference** — async bulk endpoint | README |
| **On-prem / air-gapped** — offline license keys (OCI + Helm already shipped in N) | README |

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
  L PII (done, v0.7.0)
      │
  M cost attribution (done, v0.8.0)
      │
  v0.10–v0.13 complete-product track (FULL.md)
      │
  v1.0.0 seal (partner RC sign-off, runs last)
      │
  semantic cache · batch · offline license keys · billing extras (post-v1.0)
  Edge / prefix-cache / KV-aware  (deferred)
```

## Notes

- **Mission gate:** every item must improve reliability, operator control, or
  visibility without application code changes.
- **Shipping cadence:** one milestone = one branch = one PR = release-note entry.
  Tests + docs + validation recipe required before merge.
