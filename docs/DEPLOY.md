# Deploy Meridian (Milestone N)

## Docker (single node)

```bash
docker pull ghcr.io/imv-in/meridian:0.9.2
docker run --rm -p 8080:8080 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e MERIDIAN_CONFIG=/app/config.yaml \
  ghcr.io/imv-in/meridian:0.9.2
```

Non-root image (uid 10001) + `HEALTHCHECK` on `/meridian/status`.

## Helm

```bash
helm upgrade --install meridian ./deploy/helm/meridian \
  --set image.tag=0.9.2 \
  --namespace meridian --create-namespace
```

- ConfigMap: gateway YAML (`values.config`)
- Optional Secret: `keys.yaml` mounted at `/secrets` → set `auth.keys_file: /secrets/keys.yaml`
- PVC: JSONL + cost/budget sqlite under `/var/lib/meridian`

Template check (no cluster required):

```bash
helm template meridian ./deploy/helm/meridian >/dev/null
```

## TLS (edge)

Terminate TLS at nginx/Traefik; proxy to Meridian HTTP. Example nginx:

```nginx
location / {
  proxy_pass http://meridian:8080;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_buffering off;   # streaming SSE
  proxy_read_timeout 3600s;
}
```

## Key rotation (no full restart)

1. Put keys in a separate file:

```yaml
# /secrets/keys.yaml
keys:
  - key: "mrdn_..."
    org_id: "acme"
  - key: "mrdn_..."
    org_id: "ops"
    ops_admin: true
```

```yaml
# config.yaml
auth:
  enabled: true
  keys_file: /secrets/keys.yaml
  keys: []   # optional inline keys still work; merged with file
```

2. Reload without dropping the process:
   - **SIGHUP** to the Meridian PID, or
   - `POST /meridian/reload` with an `ops_admin` Bearer key

In-flight requests keep their already-resolved identity; new requests use the new index.

## Backup / retention

| Path | Content | Suggested retention |
|------|---------|---------------------|
| `logging.jsonl_path` | Request metadata JSONL (no prompts by default) | 7–30 days hot; rotate daily |
| `budgets.sqlite_path` | Budget counters | Daily backup; period buckets auto-expire old rows on write |
| `cost.sqlite_path` | Cost ledger (enterprise: **sqlite**, not memory) | Per finance policy; daily backup + WAL |

Backup with the same cadence as other DB files. Cost ledger uses SQLite WAL — include `-wal`/`-shm` or checkpoint first. Full recipes: [`OPS_RUNBOOK.md`](./OPS_RUNBOOK.md).

## Sizing (starting point)

| Concurrent streams | CPU | RAM |
|--------------------|-----|-----|
| ~50 | 0.5–1 core | 256–512 Mi |
| ~200 | 1–2 cores | 512 Mi–1 Gi |
| ~500 | 2–4 cores | 1–2 Gi |

Gateway is mostly I/O bound; size backends/GPUs separately. Measure overhead with
`python scripts/bench_overhead.py` and see [`LOAD.md`](./LOAD.md).

## Response headers (ops)

| Header | Meaning |
|--------|---------|
| `x-request-id` | Meridian request id |
| `x-meridian-backend` | Backend that served the request |
| `x-meridian-tier` | Workload tier (if tiering on) |
| `x-meridian-session-route` | Affinity route (if session affinity on) |
| `x-meridian-budget-remaining-tokens` | Tightest token headroom after pre-flight debit |
| `x-meridian-budget-remaining-requests` | Tightest request headroom after pre-flight debit |

## Enterprise cost

See [`ENTERPRISE_COST.md`](./ENTERPRISE_COST.md) before enabling `cost:`. Day-2:
[`OPS_RUNBOOK.md`](./OPS_RUNBOOK.md).
