# Meridian — Ship Log

A scannable record of **what's shipped** and **what's next**. For the full
strategic picture see [`ROADMAP.md`](./ROADMAP.md); for per-change detail see
[`../CHANGELOG.md`](../CHANGELOG.md). Keep this file updated as milestones land.

_Last updated: 2026-07-10 — **v0.8.0 tagged** (Milestone M); N in progress._

---

## Shipped (tagged)

| Milestone | Tag | What |
|---|---|---|
| Core gateway | `v0.1.0` | OpenAI-compatible API, SSE streaming, health/failover, Prometheus, JSONL logs, operator UI |
| **A** — Distribution | `v0.1.1` | Multi-arch Docker images, smoke test, release CI |
| **B** — Token-aware routing | `v0.2.0` | Route by estimated request cost |
| **C** — Telemetry adapters | `v0.2.1` | Capacity-aware routing inputs + queue/mem penalties |
| **D** — Workload tiering | `v0.3.0` | Backend pools by request shape |
| **E** — KV-affinity lite | `v0.3.1` | Session stickiness via `x-meridian-session` |
| **F–H** — Identity keystone | `v0.4.0` | API-key auth, identity logging, per-org rate limiting |
| **I–J** — Tenant governance | `v0.5.0` | Model allow-lists + org→team→user budgets |
| **K** — Hardening | `v0.6.0` | Bounded RL store, stream-safe cleanup, body cap, non-root image |
| **L** — PII (India pack) | **`v0.7.0`** | Aadhaar/PAN/GSTIN/IFSC/UPI/phone; block/redact/audit; counts-only logs |
| **M** — Cost attribution | **`v0.8.0`** | Actual usage ledger, `/meridian/usage` + CSV, enterprise authz |
| **N** — Packaging | `v0.9.0` unreleased | Helm chart, air-gap bundle, keys_file + reload |

Also: tamper-evident audit pipeline (Kafka → hash chain → Merkle → Ed25519 → S3 WORM).

**Latest release:** `v0.8.0` — `ghcr.io/imv-in/meridian:0.8.0`

---

## Next up

| Milestone | Status | What |
|---|---|---|
| **N** — Packaging | in PR | Helm, air-gap, key reload → tag `v0.9.0` after merge |
| **v1.0** | gate | Design-partner PoC; pitch = tagged code only. |
