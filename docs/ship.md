# Meridian — Ship Log

A scannable record of **what's shipped** and **what's next**.

| Want | Read |
|------|------|
| **Full story (what + why for every milestone)** | **[`MILESTONES.md`](./MILESTONES.md)** |
| Backlog order | [`ROADMAP.md`](./ROADMAP.md) |
| Release-note detail | [`../CHANGELOG.md`](../CHANGELOG.md) |
| Pitchable claims only | [`PITCH.md`](./PITCH.md) |

_Last updated: 2026-07-10 — **v0.9.0 tagged**; **0.9.1** product-complete track (v1.0 later)._

---

## Shipped (tagged)

| Milestone | Tag | What |
|---|---|---|
| Core gateway | `v0.1.0` | OpenAI API, SSE, health/failover, metrics, UI |
| **A** — Distribution | `v0.1.1` | Multi-arch images, smoke, release CI |
| **B** — Token-aware routing | `v0.2.0` | Cost-aware strategy |
| **C** — Telemetry | `v0.2.1` | Capacity signals |
| **D** — Workload tiering | `v0.3.0` | Pools by request shape |
| **E** — Session affinity | `v0.3.1` | KV-affinity lite |
| **F–H** — Identity | `v0.4.0` | Auth, identity logs, per-org RL |
| **I–J** — Governance | `v0.5.0` | Model allow-lists, budgets |
| **K** — Hardening | `v0.6.0` | RL store, stream safety, body cap, non-root |
| **L** — PII India | `v0.7.0` | Detect/redact/block, counts-only logs |
| **M** — Cost | `v0.8.0` | Actual usage, usage API + authz |
| **N** — Packaging | **`v0.9.0`** | Helm, air-gap, keys_file + reload |

Also: tamper-evident audit pipeline (optional).

**Latest release:** `v0.9.0` — `ghcr.io/imv-in/meridian:0.9.0`

---

## Next: 0.9.x product completion (not 1.0 yet)

v1.0 is a **later verification gate** (PoC proof + pitch honesty), not the next
ship. Continue **0.9.x** until the product is complete for multi-tenant enterprise:

| Track | Status | Focus |
|---|---|---|
| **0.9.1** | in progress | Enterprise template, version endpoint, cost+auth hard gate, docs/API completeness |
| **0.9.2+** | planned | Budget↔actual reconcile, load numbers, broader e2e, more ops polish |
| **v1.0** | deferred | Design-partner PoC done; every pitch claim on a tagged image |

See [`ROADMAP.md`](./ROADMAP.md) and [`MILESTONES.md`](./MILESTONES.md).
