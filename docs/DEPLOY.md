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
  --set image.tag=0.10.0 \
  --namespace meridian --create-namespace
```

- ConfigMap: gateway YAML (`values.config`)
- Optional Secret: `keys.yaml` mounted at `/secrets` → set `auth.keys_file: /secrets/keys.yaml`
- PVC: JSONL + cost/budget sqlite under `/var/lib/meridian`

Hardened primitives in the chart (all off by default where they touch scheduling):

- `podDisruptionBudget.enabled` — keep a replica through node drains
- `topologySpreadConstraints` — spread replicas across zones/nodes
- `serviceAccount` — least-privilege identity; automount **off** (Meridian needs no K8s API)
- `ingress.enabled` (+ `tls`, cert-manager annotation) — see TLS section
- `grafana.enabled` — ships the bundled dashboards as a sidecar-watched ConfigMap
- `keda.enabled` — ScaledObject on gateway inflight (see Autoscaling)

Template check (no cluster required):

```bash
helm template meridian ./deploy/helm/meridian >/dev/null
```

## TLS (edge)

Terminate TLS at nginx/Traefik; proxy to Meridian HTTP. Or let the chart's
Ingress do it with cert-manager:

```yaml
# values override
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: meridian.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: meridian-tls
      hosts: [meridian.example.com]
```

Raw nginx example:

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

## Autoscaling

Two distinct layers — don't confuse them:

### 1) Scale the GATEWAY (stateless, CPU/queue-bound)

KEDA on the gateway's inflight metric (chart ships it, off by default):

```yaml
keda:
  enabled: true
  maxReplicas: 5
  prometheusAddress: "http://prometheus-server.prometheus.svc:9090"
  query: "sum(meridian_backend_inflight)"
  threshold: "50"          # inflight requests per replica to scale past
```

Plain HPA equivalent (no KEDA):

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: meridian
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: meridian
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Pods
      pods:
        metric:
          name: meridian_backend_inflight   # requires prometheus-adapter
        target:
          type: AverageValue
          averageValue: "50"
```

### 2) Scale BACKENDS to zero (GPU pods, idle cost)

Gateway marks a backend `idle` after `idle_timeout_min` without traffic — it
stops routing there, pauses health checks (a scaled-to-zero pod SHOULD fail
them), and exports `meridian_backend_idle{name}=1`. Point a KEDA ScaledObject
for each backend Deployment at that gauge:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: vllm-fairytale-scaler
spec:
  scaleTargetRef:
    name: vllm-fairytale
  minReplicaCount: 0          # the point: zero when idle
  maxReplicaCount: 1
  triggers:
    - type: prometheus
      metadata:
        serverAddress: http://prometheus-server.prometheus.svc:9090
        query: '1 - meridian_backend_idle{backend="vllm-fairytale"}'
        threshold: "0.5"      # idle=1 → scale to 0; active=1 → scale to 1
```

```yaml
# meridian config.yaml
backends:
  - name: vllm-fairytale
    url: http://vllm-fairytale:8000
    model: my-model
    idle_timeout_min: 30    # quiet for 30 min → idle → KEDA scales pod to 0
```

Wake-up: the first request matching the idle backend's model re-marks it active
(gauge → 0 → KEDA scales up) and routes to it immediately — early attempts may
502 while the engine cold-starts; `resilience.max_retries` + `timeouts.connect`
smooth the window. Requests keep flowing to any *active* backend meanwhile.

## Dashboards (Grafana)

Pre-built dashboards live in `deploy/grafana/`:

- **Gateway Overview** — request rate by backend/model, p50/p95 latency,
  inflight, error rate, retries, circuits, idle backends
- **Governance & Cost** — budget rejections/reconciles, PII detections,
  attributed tokens by model

Import the JSONs directly (choose the Prometheus datasource when prompted), or
deploy via the chart: `grafana.enabled=true` ships them as a
`grafana_dashboard: "1"` labeled ConfigMap for the Grafana dashboards sidecar —
tune the label with `grafana.sidecarLabel`. Every metric they use is emitted by
the gateway's `/metrics` endpoint out of the box.

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

## Config reload (no restart)

`POST /meridian/reload` (or SIGHUP) re-reads the config file and applies it
**atomically** — an invalid file is rejected (`400`) and the running state is
kept. When the gateway was started from an in-memory config (tests), reload
falls back to keys-only and reports `"scope": "keys"`.

Applies without restart: routing strategy + weights, `backends` (list,
weights, tags, timeouts), `tiering` rules, auth keys, `health` thresholds,
`resilience` knobs, `session_affinity`, `rate_limit` store bounds, and
telemetry adapters. Response:

```json
{"reloaded": true, "scope": "full", "keys": 3}
```

Budget/cost stores, the audit bus, and the JSONL log path keep their running
objects (data continuity); changing those sections logs a "requires restart"
warning. Full field reference: [`CONFIGURATION.md`](./CONFIGURATION.md).

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
