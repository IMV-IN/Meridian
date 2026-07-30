# Meridian — Ship Log

A scannable record of **what's shipped** and **what's next**.

| Want | Read |
|------|------|
| **Docs index** | **[`index.md`](./index.md)** |
| **First run** | [`QUICKSTART.md`](./QUICKSTART.md) |
| **Full story (what + why for every milestone)** | [`MILESTONES.md`](./MILESTONES.md) |
| Backlog order | [`ROADMAP.md`](./ROADMAP.md) |
| Release-note detail | [`CHANGELOG.md`](https://github.com/IMV-IN/Meridian/blob/main/CHANGELOG.md) |
| Pitchable claims only | [`internal/PITCH.md`](./internal/PITCH.md) |

_Last updated: 2026-07-28 — **v0.11.0 in tree**; Grafana dashboards, Helm hardening + KEDA scaffolding, scale-to-zero idle marker; next: Phase 3 platform depth (v0.12.0)._

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
| **0.9.2** | `v0.9.2` | Budget ↔ actual token-meter reconcile |
| **0.9.3** | `v0.9.3` | Load harness, enterprise e2e, ops polish |
| **0.9.4** | **`v0.9.4`** | RBAC roles, ops-endpoint auth, dashboard overhaul, scenario quickstart |
| **0.10.0** | `v0.10.0` | Circuit breaker, retry+backoff, upstream/stream timeouts, full config reload; Phase 0 code health (fd fix, `[audit]` extra, 87% cov) |
| **0.11.0** | **`v0.11.0`** | Grafana dashboards, Helm PDB/Ingress/TLS/RBAC, KEDA scaffolding, scale-to-zero idle marker |
| **0.12.0** | **`v0.12.0`** | Tenant isolation modes, canary rollouts, key lifecycle API, per-key budgets/usage, per-model+global rate limits; branch-wide security/reliability hardening |

Also: optional audit pipeline; Ollama load numbers ([`LOAD.md`](./LOAD.md)); hardened image scan ([`scans/IMAGE_SCAN_0.9.3.md`](./scans/IMAGE_SCAN_0.9.3.md)).

**Latest release:** `v0.12.0` — `ghcr.io/imv-in/meridian:0.12.0`

---

## Next (features paused)

| Track | Status | Focus |
|---|---|---|
| **0.9.1–0.9.3** | **shipped** | Product-complete 0.9.x track |
| **Ollama load proof** | **done** | [`LOAD.md`](./LOAD.md) |
| **Design-partner PoC** | **done (lab)** | [`internal/POC_REPORT.md`](./internal/POC_REPORT.md) |
| **Image scan** | **done** | **0 CRITICAL** — [`scans/IMAGE_SCAN_0.9.3.md`](./scans/IMAGE_SCAN_0.9.3.md) |
| **Quickstart DX** | **done** | Slim compose on :8080, docs index, no Kafka by default |
| **v1.0 gate** | **hold** | Cofounder/partner sign-off — [`internal/V1_GATE.md`](./internal/V1_GATE.md) |

See [`ROADMAP.md`](./ROADMAP.md) and [`MILESTONES.md`](./MILESTONES.md).
