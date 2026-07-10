# Meridian

**Latest release: [v0.9.3](https://github.com/IMV-IN/Meridian/releases/tag/v0.9.3)** ·  
**Docs index:** [`docs/README.md`](docs/README.md) · **Quickstart:** [`docs/QUICKSTART.md`](docs/QUICKSTART.md)

Meridian is an **L7 inference gateway** for self-hosted / on-soil LLM fleets. It sits between your apps and OpenAI-compatible backends (vLLM, SGLang, TensorRT-LLM, Ollama, …) and adds **routing, health/failover, multi-tenant controls, and compliance hooks** without changing application code.

It is **not** an inference engine — no GPU scheduling, no KV-cache allocator.

> Think: *nginx for LLM backends, with enterprise controls baked in.*

## Why teams use it

- **Drop-in OpenAI API** — `/v1/chat/completions` (stream + non-stream), `/v1/models`
- **Routing & reliability** — least-inflight, token-aware, EWMA; health checks + failover
- **Multi-tenant controls** — API keys → org/team/user; budgets; model allow-lists; rate limits
- **Compliance hooks** — India PII pack (request path); optional tamper-evident audit; metadata-only logs
- **Cost** — actual `usage` ledger + org-scoped export (`docs/ENTERPRISE_COST.md`)
- **Ops** — Prometheus, Helm, air-gap packaging, non-root image

Full feature history: [`docs/MILESTONES.md`](docs/MILESTONES.md) · Status: [`docs/ship.md`](docs/ship.md)

## 5-minute quickstart

```bash
git clone https://github.com/IMV-IN/Meridian.git && cd Meridian
docker compose up --build
```

Then (gateway on **http://localhost:8080** — not 9080):

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
```

Look for **`x-meridian-backend`** (`fast` or `slow`) and **`x-request-id`**.  
Dashboard: http://localhost:8080/ui  

**Details, smoke test, stop/start:** [`docs/QUICKSTART.md`](docs/QUICKSTART.md)

This compose stack is **gateway + 2 mock backends only** (no Kafka).  
Optional Kafka/audit: `docker compose -f docker-compose.kafka-demo.yml up --build` or `docker-compose.audit.yaml`.

## Install without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -e .

# Point at a config (always MERIDIAN_CONFIG — uppercase)
export MERIDIAN_CONFIG=configs/mock_demo.yaml
# For local mocks, start mock_backend processes first, or use docker compose
# for backends only and point URLs at localhost.

uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080
# CLI helper (sets MERIDIAN_CONFIG):
#   meridian --config configs/local_gpu.yaml
```

Published images: `ghcr.io/imv-in/meridian:0.9.3` / `:latest`  
```bash
docker run --rm -p 8080:8080 \
  -v "$(pwd)/configs/mock_demo.yaml:/app/config.yaml:ro" \
  -e MERIDIAN_CONFIG=/app/config.yaml \
  ghcr.io/imv-in/meridian:0.9.3
```
(You’ll still need reachable backends in that config.)

## Real backend (Ollama)

```bash
ollama pull qwen2.5:0.5b && ollama serve
export MERIDIAN_CONFIG=configs/local_gpu.yaml
uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080
```

Load / overhead numbers: [`docs/LOAD.md`](docs/LOAD.md)

## Documentation map

| Path | Audience |
|------|----------|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | First successful run |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Full config + auth/budgets/PII examples |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) · [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md) | Production |
| [`docs/README.md`](docs/README.md) | **Index of all docs** |
| [`docs/internal/`](docs/internal/) | Pitch, PoC report, v1.0 gate (not product manuals) |

## API (short)

| Endpoint | Description |
|----------|-------------|
| `POST /v1/chat/completions` | Chat (stream + non-stream) |
| `GET /v1/models` | Models |
| `GET /meridian/status` · `/meridian/version` | Ops |
| `GET /metrics` · `/ui` | Prometheus + dashboard |

Proxied responses include `x-request-id` and `x-meridian-backend`.

## Architecture

```
Client → Meridian (FastAPI): route · policy · proxy · metrics
       → Backend 1…N (vLLM / Ollama / any OpenAI-compatible)
```

## Development

```bash
pip install -e ".[dev]"
ruff check . && mypy meridian && pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
