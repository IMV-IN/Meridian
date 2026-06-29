# Meridian

Meridian is an **L7 inference gateway** that sits between your applications and multiple inference backends (vLLM, SGLang, TensorRT-LLM, or any OpenAI-compatible server). It provides:

- A single API endpoint that routes to the best available backend
- Automatic health checking and failover when backends go down
- Streaming SSE passthrough with minimal overhead
- Prometheus metrics and structured audit logs for production visibility

Meridian is **not** an inference engine — it doesn't manage KV cache, batching, or GPU scheduling. It's the routing and reliability layer that makes your existing backends production-ready.

## Features

- **OpenAI-compatible API** — drop-in replacement (`/v1/chat/completions`, `/v1/models`)
- **Streaming SSE passthrough** — zero-copy byte forwarding, no parsing
- **4 routing strategies** — weighted round-robin, least inflight, EWMA latency, **token-aware**
- **Health checking & failover** — active pings + passive failure detection
- **Prometheus metrics** — request counters, latency histograms, backend health gauges
- **JSONL request logs** — every request logged with backend, latency, status
- **Tamper-evident audit pipeline** — optional async egress to Kafka/Redpanda, SHA-256 hash chain → Merkle tree → Ed25519 signing → S3 Object Lock (WORM); metadata-only
- **Live dashboard** — real-time UI showing backend health, stats, and recent requests
- **Rate limiting** — basic token bucket for now, will be upgraded to support org, team
- **API-key authentication** — opt-in Bearer-key enforcement on `/v1/*`; each key maps to an org/team/user identity (attached to logs as metadata); disabled by default for backward compatibility

### Coming soon

- **Multi-provider routing** — OpenAI, Anthropic, Google + self-hosted backends through one endpoint
- **Provider-specific cost tracking** — per-provider token pricing, per-team attribution
- **Semantic caching** — cache similar prompts at the gateway level
- **PII detection & redaction** — jurisdiction-specific entity packs
- **RBAC** — org → team → user hierarchy with budget caps
- **Batch inference** — async endpoint for bulk processing
- **On-prem deployment** — OCI containers + Helm charts, air-gapped mode

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

> **Scope:** authentication enforcement + identity-aware logging are shipped. Per-identity rate limiting (re-keying the token bucket on `org_id`/`team_id`) is planned for a later slice.

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
| `/meridian/status` | GET | Backend health, inflight counts, EWMA latency |
| `/meridian/requests` | GET | Recent requests (in-memory ring buffer, last 100) |
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
