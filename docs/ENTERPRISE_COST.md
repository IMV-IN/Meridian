# Enterprise checklist — cost attribution (Milestone M)

Use this before enabling cost tracking for a multi-tenant production gateway
(~hundreds–thousands of users).

## Hard requirements (enforced)

- **`cost.enabled` requires `auth.enabled`** — Meridian refuses to start otherwise.
- **Usage APIs require a valid Bearer key** — no anonymous export.
- Prefer **`store: sqlite`** in production (memory is lost on restart).

## Required config

```yaml
auth:
  enabled: true
  keys:
    - key: "mrdn_..."          # app traffic
      org_id: "acme"
      team_id: "eng"           # optional; locks usage view to this team
    - key: "mrdn_..."          # finance export only
      org_id: "finance"
      cost_admin: true         # may query all orgs

cost:
  enabled: true
  store: sqlite                # NOT memory — survives restart
  sqlite_path: /var/lib/meridian/meridian_cost.db
  currency: USD
  max_window_days: 366
  default_prompt_per_1m: 0.15
  default_completion_per_1m: 0.60
  models:
    your-model-id:
      prompt_per_1m: 0.10
      completion_per_1m: 0.40
```

## Access control (enforced in code)

| Caller | `/meridian/usage` / `.csv` |
|--------|----------------------------|
| No Bearer / bad key | **401** |
| `cost.enabled` but `auth.enabled=false` | **401** (export refused) |
| Normal key | Own **org** only (team forced if key has `team_id`) |
| Other org in query | **403** |
| `cost_admin: true` | Any org/team or all rows |

`/meridian/status`, `/ui`, `/metrics` remain open (ops plane). Put Meridian
behind a private network / LB allowlist for those paths.

## Ops

1. **Backup** `sqlite_path` with the same cadence as other stateful data.
2. **Disk**: WAL mode is enabled; leave room for `-wal` / `-shm` sidecars.
3. **Do not use `store: memory`** in production (lost on restart; startup warns).
4. **Budgets vs cost ledger**: pre-flight budgets still reserve on **estimates**. As of **0.9.2**, token meters are **reconciled** to actual backend `usage` after a successful response (same weights as estimate). The cost ledger remains the finance report; budget meters remain the enforcement plane.
5. Stream usage is scraped from the SSE tail (last `usage` object wins). Prefer backends that emit OpenAI-style usage on the final chunk. No reconcile (and no ledger row) if the tail has no `usage`.
6. Upstream failures keep the pre-flight budget charge (no refund on 502 / disconnect).

## Smoke validation

```bash
# 401 without key
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/meridian/usage

# 200 own org
curl -s -H "Authorization: Bearer mrdn_..." \
  "http://localhost:8080/meridian/usage?window_days=7" | head

# CSV
curl -s -H "Authorization: Bearer mrdn_..." \
  -o usage.csv "http://localhost:8080/meridian/usage.csv"
```
