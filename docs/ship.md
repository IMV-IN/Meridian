# Meridian — Ship Log

A scannable record of **what's shipped** and **what's next**.

| Want | Read |
|------|------|
| **Full story (what + why for every milestone)** | **[`MILESTONES.md`](./MILESTONES.md)** |
| Backlog order | [`ROADMAP.md`](./ROADMAP.md) |
| Release-note detail | [`../CHANGELOG.md`](../CHANGELOG.md) |
| Pitchable claims only | [`PITCH.md`](./PITCH.md) |

_Last updated: 2026-07-10 — **v0.9.2 tagged**; **0.9.3** load / e2e / ops polish (v1.0 later)._

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
| **N** — Packaging | `v0.9.0` | Helm, air-gap, keys_file + reload |
| **0.9.1** | `v0.9.1` | Version endpoint, enterprise template, cost↔auth gate |
| **0.9.2** | **`v0.9.2`** | Budget ↔ actual token-meter reconcile |

Also: tamper-evident audit pipeline (optional).

**Latest release:** `v0.9.2` — `ghcr.io/imv-in/meridian:0.9.2`

---

## Next: 0.9.x product completion (not 1.0 yet)

| Track | Status | Focus |
|---|---|---|
| **0.9.1** | **shipped** | Enterprise template, version endpoint, cost+auth hard gate |
| **0.9.2** | **shipped** | Budget↔actual token-meter reconcile |
| **0.9.3** | in progress | Load numbers, richer e2e, ops polish |
| **v1.0** | deferred | Design-partner PoC done; every pitch claim on a tagged image |

See [`ROADMAP.md`](./ROADMAP.md) and [`MILESTONES.md`](./MILESTONES.md).
