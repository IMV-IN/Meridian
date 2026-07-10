# Meridian

**Latest release: [v0.9.0](https://github.com/IMV-IN/Meridian/releases/tag/v0.9.0)** ·  
**Full milestone history (what + why):** [`docs/MILESTONES.md`](docs/MILESTONES.md) ·  
Ship log: [`docs/ship.md`](docs/ship.md) · Deploy: [`docs/DEPLOY.md`](docs/DEPLOY.md)

Meridian is an **L7 inference gateway** for on-soil / self-hosted LLM fleets. It sits between your applications and multiple inference backends (vLLM, SGLang, TensorRT-LLM, Ollama, or any OpenAI-compatible server) and adds **routing, reliability, multi-tenant controls, and compliance hooks** without changing application code.

Meridian is **not** an inference engine — it does not manage KV cache, batching, or GPU scheduling.

## Features (shipped through v0.7.0)

### Core gateway
- **OpenAI-compatible API** — `/v1/chat/completions`, `/v1/models`
- **Streaming SSE passthrough** — zero-copy byte forwarding
- **Routing strategies** — weighted round-robin, least inflight, EWMA latency, **token-aware**
- **Workload tiering** — pools by prompt/decode shape; **session affinity** (`x-meridian-session`)
- **Health checks & failover** — active + passive
- **Telemetry-aware scoring** — optional capacity penalties from backend JSON signals
- **Prometheus + JSONL** — metadata only (no prompts by default)
- **Operator UI** — `/ui` backend health and recent requests
- **Tamper-evident audit pipeline** — optional Kafka → hash chain → Merkle → Ed25519 → S3 WORM

### Multi-tenant controls
- **API-key auth** — opt-in Bearer keys → org/team/user identity
- **Identity-aware logs & rate limits** — per-org buckets when auth is on
- **Model access control** — per-key `allowed_models` → 403
- **Tenant budgets** — org→team→user daily/monthly tokens + requests; pre-flight 429

### Compliance & hardening
- **PII (India pack)** — Aadhaar (Verhoeff), PAN, GSTIN, IFSC, UPI, mobile; policies `block` / `redact_and_replace` / `audit_only`; **matched values never logged**
- **Pilot hardening** — bounded rate-limit store, stream-disconnect-safe cleanup, body size cap, non-root container + `HEALTHCHECK`

### Cost attribution (Milestone M — `v0.8.0`)
- Opt-in `cost.enabled`; prices per model (per 1M prompt/completion tokens)
- Scrapes backend `usage` on non-stream responses and stream SSE tails (last usage wins)
- `GET /meridian/usage` + `GET /meridian/usage.csv` — **auth required**; org-scoped unless `cost_admin: true`
- `cost.enabled` **requires** `auth.enabled` at startup
- Enterprise checklist: [`docs/ENTERPRISE_COST.md`](docs/ENTERPRISE_COST.md)

### Deploy / packaging (Milestone N — `v0.9.0`)
- Helm chart: `deploy/helm/meridian/`
- Air-gap bundle: `scripts/package_airgap.sh` + `docs/AIRGAP.md`
- Key hot-reload: `auth.keys_file` + SIGHUP or `POST /meridian/reload` (`ops_admin`)
- Enterprise config template: `configs/enterprise_example.yaml`

### Budget ↔ actual (0.9.2)
- Pre-flight budgets reserve on estimated cost; after a successful response with backend `usage`, **token** meters adjust to actual (`prompt * prefill_weight + completion * decode_weight`). Request counters and failed requests are not refunded.

### Load, e2e, ops (0.9.3)
- Overhead bench: `python scripts/bench_overhead.py` + [`docs/LOAD.md`](docs/LOAD.md)
- Enterprise e2e tests + CI gateway smoke; budget remaining response headers
- Day-2 ops: [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md)

### Coming later (not product-complete / not 1.0 yet)
- **Multi-provider routing**, **semantic caching**, **batch inference**
- Design-partner v1.0 gate

## 10-Minute Quickstart

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed
- Ports 8080, 9001, 9002 available

### Option A — Pull the published image

```bash
# Pull the latest published image
docker pull lothnic0801/meridian:latest

# Run with your own config (replace ./config.yaml with your file)
docker run --rm -p 8080:8080 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e meridian_CONFIG=/app/config.yaml \
  lothnic0801/meridian:latest
```

Images are published for `linux/amd64` and `linux/arm64`. Tags: `latest` and per-release `vX.Y.Z`.
Mirror on GHCR: `ghcr.io/imv-in/meridian:latest`.

### Option B — Compose demo (gateway + 2 mock backends)

```bash
git clone https://github.com/IMV-IN/Meridian.git && cd Meridian
docker compose up --build
```

This starts:
- **meridian** gateway on `localhost:8080`
- **backend-fast** mock (50ms latency) on `localhost:9001`
- **backend-slow** mock (300ms latency) on `localhost:9002`

### Test it

```bash
# Non-streaming request
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'

# Streaming request
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Count to 5"}],"stream":true}'

# Concurrent requests (see load balancing in action)
for i in {1..10}; do
  curl -s http://localhost:8080/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"demo-model","messages":[{"role":"user","content":"concurrent '$i'"}]}' \
    -o /dev/null -w "Request $i: backend=%header{x-meridian-backend}\n" &
done
wait

# Check metrics
curl -s http://localhost:8080/metrics | grep meridian_

# Backend status
curl -s http://localhost:8080/meridian/status | python3 -m json.tool

# Recent requests (in-memory ring buffer, last 100)
curl -s http://localhost:8080/meridian/requests | python3 -m json.tool
```

### Smoke test

A scripted check (`/v1/models`, non-stream and streaming chat completions, required headers) is included:

```bash
python scripts/smoke_test.py --url http://localhost:8080 --model demo-model
```

Exits 0 on success, prints `FAIL:`/`ERROR:` and exits 1 otherwise. Used by the release workflow to validate published images.

### Live dashboard

Open `http://localhost:8080/ui` in your browser. The dashboard polls every second and shows:
- Backend health status, inflight counts, EWMA latency, and weights
- Recent requests table with request ID, backend, model, stream, status, latency, and timestamp

### Failover demo

```bash
# Stop the fast backend
docker stop meridian-v1-backend-fast-1

# Wait ~10s for health checker to detect (2 consecutive failures)
# Dashboard will show "fast" as Unhealthy

# Requests now route to slow backend automatically
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
# x-meridian-backend: slow

# Bring it back
docker start meridian-v1-backend-fast-1
# Wait ~5s, fast is healthy again
```

## Real GPU Backend

Meridian works with any OpenAI-compatible backend. The easiest way to test with a real GPU is [Ollama](https://ollama.com/):

```bash
# 1. Install Ollama and pull a small model
ollama pull qwen2.5:0.5b
ollama serve

# 2. Start Meridian with the GPU config
meridian_CONFIG=configs/local_gpu.yaml uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080

# 3. Send a request
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Say hi in one sentence."}]}'
```

For vLLM, SGLang, or TensorRT-LLM backends, update `configs/local_gpu.yaml` with the backend URL and model name.

For dual-backend failover testing with a single GPU, see `configs/dual_backend.yaml` and `delay_proxy.py`.

## Local Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run checks
ruff check .
mypy meridian
pytest -v

# Start dev server
uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080 --reload
```

## Configuration

Create a `config.yaml` (or use one from the `configs/` directory):

```yaml
gateway:
  host: "0.0.0.0"
  port: 8080
  strategy: "least_inflight"  # weighted_round_robin | least_inflight | ewma_latency | token_aware

  # Token-aware routing knobs (only used when strategy: "token_aware").
  # Score = (backend.inflight_cost + request_cost) * (ewma_latency or 1.0)
  # request_cost = prompt_tokens * prefill_weight + max_tokens * decode_weight
  prefill_weight: 1.0          # weight applied to estimated prompt tokens
  decode_weight: 4.0           # weight applied to max_tokens (decode is more expensive per token)
  default_max_tokens: 256      # used when the request doesn't set max_tokens
  token_estimator: "heuristic" # only "heuristic" implemented today

  # Capacity-aware penalties (added to the token_aware base score when backend
  # telemetry exposes the corresponding signal). Default 0.0 = telemetry has
  # no routing effect unless an operator tunes these.
  queue_weight: 0.0            # multiplied by reported queue_depth
  mem_weight: 0.0              # multiplied by reported gpu_mem_util (0.0-1.0)

# Workload tiering (optional, disabled by default). Routes requests to backend
# pools by request shape. A request maps to "long_prompt" when its estimated
# prompt size >= long_prompt_tokens, else "long_decode" when max_tokens >=
# long_decode_tokens, else "default". Precedence is fixed (long_prompt first).
# Each tier maps to backend tags; if the matched tier has no healthy backend,
# Meridian falls back to all healthy backends (reliability over isolation). The
# chosen tier is surfaced on the `x-meridian-tier` response header and in logs.
tiering:
  enabled: false
  long_prompt_tokens: 4000
  long_decode_tokens: 1000
  tiers:
    long_prompt: ["prefill-pool"]
    long_decode: ["decode-pool"]
    default: ["general"]

# Session affinity (optional, disabled by default). Pins a session to one backend
# for KV-cache reuse. Requests carrying the `header` route to the same backend
# while it stays healthy. Sliding TTL: each use refreshes the idle timeout.
# `max_sessions` bounds memory; `sweep_interval_s` controls background eviction.
# If the pinned backend becomes unhealthy, requests remap to another healthy
# backend. Affinity state is surfaced via `x-meridian-session-route` header.
session_affinity:
  enabled: false
  header: "x-meridian-session"
  ttl_s: 600.0
  sweep_interval_s: 60.0
  max_sessions: 100000

# API-key authentication (optional, disabled by default). When enabled, every
# request to /v1/* must carry Authorization: Bearer <key>. Keys are matched
# against this list; unrecognised or missing keys get HTTP 401. Each key maps
# to an identity (org_id required, team_id and user_id optional). Duplicate
# keys are rejected at config load. The /metrics, /meridian/*, and /ui
# endpoints are always open (no auth gate). Key format: mrdn_ followed by
# 20-40 alphanumeric characters.
auth:
  enabled: false
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      team_id: "eng"
      user_id: "alice"
    - key: "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
      org_id: "acme"

health:
  interval_s: 5
  timeout_s: 2
  fail_threshold: 2
  success_threshold: 1

logging:
  level: "INFO"
  jsonl_path: "./meridian_requests.jsonl"

backends:
  - name: "fast"
    url: "http://backend-fast:9001"
    engine: "vllm"
    model: "demo-model"
    weight: 80
    tags: ["fast"]
    health_endpoint: "/v1/models"
    # Optional: tell Meridian to scrape capacity signals from the backend.
    # Failures here NEVER mark the backend unhealthy — telemetry is purely a
    # routing-preference signal and falls back safely when missing.
    telemetry:
      type: "json"
      url: "http://backend-fast:9001/stats"
      interval_s: 5.0
      timeout_s: 2.0

  - name: "slow"
    url: "http://backend-slow:9002"
    engine: "vllm"
    model: "demo-model"
    weight: 20
    tags: ["cheap"]
    health_endpoint: "/v1/models"
```

Pre-built configs are available in the `configs/` directory:
- `configs/mock_demo.yaml` — mock backends for Docker Compose demo
- `configs/local_gpu.yaml` — single GPU backend (Ollama)
- `configs/dual_backend.yaml` — dual-backend failover testing with delay proxy
- `configs/tiering_demo.yaml` — workload tiering across prefill/decode/general pools

## API-key Authentication

Authentication is **disabled by default** and fully backward compatible — existing deployments without an `auth:` block continue to work unchanged.

When `auth.enabled: true`, every request to `/v1/*` must include a valid `Authorization: Bearer <key>` header. The `/metrics`, `/meridian/*`, and `/ui` endpoints are always open with no auth gate.

**Error responses** follow the OpenAI error shape:

- Missing or malformed header → HTTP 401, `"type": "invalid_request_error"`
- Header present but key not found → HTTP 401, `"type": "authentication_error"`

**Key format:** `mrdn_` followed by 20–40 alphanumeric characters. Each key is mapped to an identity at config load (`org_id` required; `team_id` and `user_id` optional). Duplicate keys are rejected at startup.

### Identity-aware logging

When auth is enabled, the resolved identity is attached to every request's observability output as **metadata only** — the API key itself is never logged. Each JSONL line and audit event carries `org_id` and `team_id` (both `null` when auth is disabled), so operators can attribute traffic per org/team:

```json
{"request_id": "mrdn-...", "model": "demo", "chosen_backend": "vllm-a", "status_code": 200, "org_id": "acme", "team_id": "eng", ...}
```

When auth is enabled, **rate limiting keys on the caller's org** (`org:{org_id}`) instead of source IP, so a tenant shares one bucket no matter which IP its requests arrive from. With auth disabled the limiter falls back to per-IP. The same `rate_limit.token_capacity`/`token_refill_rate` apply per bucket.

### Model access control

Each key may declare an `allowed_models` allow-list. When set, requests for any model outside the list return **HTTP 403** (`"type": "permission_error"`); an empty or absent list leaves the key unrestricted. The gate only applies when auth is enabled.

```yaml
auth:
  enabled: true
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      team_id: "eng"
      allowed_models: ["qwen2.5:0.5b", "demo-model"]  # this key is limited to these
    - key: "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
      org_id: "acme"                                    # no list => all models
```

### Tenant budgets & quotas

Budgets are **disabled by default**. When `budgets.enabled: true`, Meridian meters each authenticated request against configured caps **before** routing to a backend. Metering uses the same estimated request cost as token-aware routing (`prompt_tokens * prefill_weight + max_tokens * decode_weight`) plus a request count — no response-body parsing, so streaming stays zero-copy.

Caps cascade **org → team → user**. A request debits every applicable level; the first exhausted level returns **HTTP 429** (`"type": "rate_limit_exceeded"`) with `Retry-After` until the UTC period rolls (daily `YYYY-MM-DD` / monthly `YYYY-MM`). Scope keys:

| Level | Config map | Key format |
|---|---|---|
| org | `budgets.orgs` | `org_id` |
| team | `budgets.teams` | `{org_id}/{team_id}` |
| user | `budgets.users` | `{org_id}/{user_id}` |

```yaml
budgets:
  enabled: true
  store: sqlite                    # or memory (ephemeral / tests)
  sqlite_path: ./meridian_usage.db
  orgs:
    acme:
      daily:
        tokens: 1000000
        requests: 5000
      monthly:
        tokens: 20000000
  teams:
    acme/eng:
      daily:
        tokens: 200000
  users:
    acme/alice:
      daily:
        requests: 200

# Per-org token-bucket overrides (not budget caps)
rate_limit:
  enabled: true
  token_capacity: 100
  token_refill_rate: 10
  org_overrides:
    acme:
      token_capacity: 20
      token_refill_rate: 5
```

Rejections increment `meridian_budget_rejections_total{level,period}` (never labeled by tenant id). Auth must be enabled for budgets to apply — without an identity there is no tenant to meter.

> **Scope:** the identity keystone (auth, identity logging, per-org rate limiting, model access, tenant budgets) is complete. See [`docs/ship.md`](docs/ship.md).

### PII detection & redaction (India pack)

**Disabled by default.** When `pii.enabled: true`, Meridian scans **request** message text only (response bodies are not scanned in v0.7) for:

| Entity | Notes |
|---|---|
| Aadhaar | 12 digits + **Verhoeff** checksum (rejects random 12-digit strings) |
| PAN | `AAAAA9999A` |
| GSTIN | 15-char GSTIN pattern |
| IFSC | Bank IFSC |
| UPI | `user@handle` |
| Indian mobile | 10-digit starting 6–9, optional `+91` |

**Policies** (global `pii.policy`, optional per-key `pii_policy` override):

| Policy | Behaviour |
|---|---|
| `block` | HTTP 400; request never reaches a backend |
| `redact_and_replace` | Mask PII in messages, then forward |
| `redact_for_logs` / `audit_only` | Forward raw; record **counts by type** only in JSONL/audit |

**Security rules:** matched values are never written to JSONL, audit events, metrics labels, or error messages. Prometheus: `meridian_pii_detections_total{entity,policy}`.

```yaml
pii:
  enabled: true
  policy: redact_and_replace   # or block | redact_for_logs | audit_only
  entities: []                 # empty = all types

auth:
  enabled: true
  keys:
    - key: "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
      org_id: "acme"
      pii_policy: block          # optional override for this key
```

### Quick curl examples

```bash
# Without a key — returns 401
curl -i http://localhost:8080/v1/models

# HTTP/1.1 401 Unauthorized
# {"error": {"message": "Missing or malformed Authorization header", "type": "invalid_request_error"}}

# With a valid key — returns 200
curl -i http://localhost:8080/v1/models \
  -H "Authorization: Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"

# HTTP/1.1 200 OK
# {"object":"list","data":[...]}

# Chat completions also require the header when auth is enabled
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
```

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions (stream + non-stream) |
| `/v1/models` | GET | List available models |
| `/meridian/status` | GET | Backend health, inflight, EWMA latency |
| `/meridian/version` | GET | Package version (ops smoke) |
| `/meridian/requests` | GET | Recent requests (ring buffer, last 100) |
| `/meridian/reload` | POST | Hot-reload API keys (`ops_admin` Bearer) |
| `/meridian/usage` | GET | Cost/token report (auth when cost on) |
| `/meridian/usage.csv` | GET | CSV export (auth when cost on) |
| `/metrics` | GET | Prometheus metrics |
| `/ui` | GET | Live dashboard |

### Response Headers

Every proxied response includes:
- `x-request-id` — unique request ID (`mrdn-...`)
- `x-meridian-backend` — name of the backend that served the request

## Architecture

```
Client → Meridian Gateway (FastAPI)
            ├── Router (strategy selection)
            ├── Registry (backend state)
            ├── Health Checker (background)
            ├── Proxy (httpx forwarding)
            └── Metrics + Logs
         → Backend 1 (vLLM/SGLang/TensorRT-LLM)
         → Backend 2
         → Backend N
```

## License

MIT — see [LICENSE](LICENSE) for details.
