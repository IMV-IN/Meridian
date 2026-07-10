# Ops runbook — multi-tenant Meridian

Day-2 operations for a ~hundreds–thousands-user gateway. Complements
[`DEPLOY.md`](./DEPLOY.md), [`ENTERPRISE_COST.md`](./ENTERPRISE_COST.md),
and [`LOAD.md`](./LOAD.md).

## Health signals

| Check | Expect |
|-------|--------|
| `GET /meridian/status` | All production backends `healthy: true` |
| `GET /meridian/version` | Matches deployed image tag |
| `GET /metrics` | `meridian_backend_healthy`, request latency histogram |
| `GET /v1/models` | 200 (with Bearer if `auth.enabled`) |

Alert ideas (cardinality-safe labels only):

- `meridian_backend_healthy == 0` for any backend
- Spike in `meridian_budget_rejections_total`
- Rise in `5xx` on `meridian_requests_total{status=~"5.."}`
- Disk filling under `logging.jsonl_path` / sqlite paths

## Auth & keys

| Action | How |
|--------|-----|
| Rotate app keys | Edit `auth.keys_file` → `SIGHUP` or `POST /meridian/reload` with `ops_admin` |
| Break-glass export | Key with `cost_admin: true` |
| Ops-only reload | Key with `ops_admin: true` |

Never put production keys in git. Prefer `keys_file` + K8s Secret (Helm chart).

## Budgets

- Caps: org → team → user; daily / monthly; tokens + requests.
- Pre-flight reserves **estimate**; **0.9.2+** reconciles **token** meters to
  backend `usage` after success.
- Response headers (when budgets hit):
  - `x-meridian-budget-remaining-tokens` — tightest token headroom after debit
  - `x-meridian-budget-remaining-requests` — tightest request headroom after debit
- On 429: `Retry-After` until period rollover (UTC).
- Failed upstream (502) **keeps** the estimate charge (no refund).

## Cost ledger

- Requires `auth.enabled` at process start.
- Export: `GET /meridian/usage` / `.csv` with Bearer; org-scoped unless `cost_admin`.
- Prefer `store: sqlite` + backup (see retention below).

## Retention & backup

| Data | Config | Retention guidance |
|------|--------|--------------------|
| Request JSONL | `logging.jsonl_path` | Rotate daily; keep 7–30 days hot; archive if compliance needs longer. **Metadata only** — no prompts by default. |
| Budget sqlite | `budgets.sqlite_path` | Backup with daily cadence; counters reset by period bucket, not file delete. |
| Cost sqlite | `cost.sqlite_path` | Finance source of truth — backup + WAL/shm; retain per finance policy (often 1–7 years offline). |
| Audit archive | audit pipeline paths | Follow WORM/compliance schedule if enabled. |

Backup recipe (sqlite):

```bash
# Prefer checkpoint then copy, or use sqlite3 .backup
sqlite3 /var/lib/meridian/meridian_cost.db ".backup '/backup/meridian_cost.db'"
sqlite3 /var/lib/meridian/meridian_budgets.db ".backup '/backup/meridian_budgets.db'"
```

JSONL: logrotate or external shipper; do not truncate an open file without rotation.

## Failover

1. Passive health marks backend unhealthy after consecutive failures.
2. Active checker re-probes `health_endpoint`.
3. Clients should retry **idempotent** reads; chat completions are **not**
   safe to blindly retry unless the app accepts duplicate generation.

Validate after deploy: kill one backend (or mark unhealthy) and confirm
`x-meridian-backend` moves to a healthy peer.

## Smoke after deploy

```bash
python scripts/smoke_test.py --url https://gateway.example --model your-model
# Enterprise path (auth):
python scripts/smoke_test.py --url https://gateway.example --model your-model \
  --auth mrdn_... --check-budget-headers
```

## Load check

```bash
python scripts/bench_overhead.py --requests 200 --concurrency 20
# Or against production-like stack — see LOAD.md
```

## Incident checklist

1. `/meridian/status` — which backends down?
2. Recent 429s — budget or rate limit? Check `Retry-After` and metrics.
3. Disk — sqlite / JSONL full?
4. Config reload vs restart — keys_file reload only swaps keys, not full YAML.
5. Roll back image tag if regression confirmed.
