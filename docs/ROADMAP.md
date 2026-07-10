# Meridian Roadmap

Single source of truth for **ordering**. Narrative history (what + why) lives in
[`MILESTONES.md`](./MILESTONES.md); one-line status in [`ship.md`](./ship.md);
remaining plans in [`V1_ROADMAP.md`](./V1_ROADMAP.md). When ordering conflicts,
this file wins.

_Last reconciled: 2026-07-10._

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

### Phase 1 — Complete the 0.9.x product (before any 1.0 talk)

| Item | Notes |
|---|---|
| **0.9.1** | **shipped** — enterprise template, `/meridian/version`, cost requires auth at boot, Helm CI |
| **0.9.2** | **shipped** — budget↔actual token-meter reconcile |
| **0.9.3** | **shipped** — load harness, enterprise e2e, ops polish |
| **Ollama real-path proof** | **done** — `LOAD.md` section (~2 ms gateway overhead on qwen2.5:0.5b) |
| **Multi-provider / semantic cache / batch** | Optional; features paused until after v1.0 gate decision |

### Phase 2 — v1.0 gate (**in progress**)

Verification only (no new product features):

| Item | Status |
|------|--------|
| PoC report on tagged image | **Done** — [`POC_REPORT.md`](./POC_REPORT.md) |
| Pitch = code | **Done** — [`PITCH.md`](./PITCH.md) |
| SECURITY current | **Done** — root `SECURITY.md` |
| Load + Ollama proof | **Done** — [`LOAD.md`](./LOAD.md) |
| Image scan | **Recorded** — 4 CRITICAL base OS (no FixedVersion); not “clean” — [`scans/IMAGE_SCAN_0.9.3.md`](./scans/IMAGE_SCAN_0.9.3.md) |
| Partner / cofounder sign-off | **Open** |
| Tag **v1.0.0** | **Hold** until scan policy + cofounder chat — see [`V1_GATE.md`](./V1_GATE.md) |

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
  L PII (done, v0.7.0)
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
