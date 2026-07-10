# Quickstart — see Meridian work in 5 minutes

**Goal:** run a gateway in front of two mock backends and send one chat request.

**You need:** Docker + Docker Compose, free ports **8080**, **9001**, **9002**.

## 1. Start

```bash
git clone https://github.com/IMV-IN/Meridian.git
cd Meridian
docker compose up --build
```

This starts only:

| Service | URL |
|---------|-----|
| Meridian gateway | http://localhost:8080 |
| Mock backend `fast` (~50 ms) | http://localhost:9001 |
| Mock backend `slow` (~300 ms) | http://localhost:9002 |

No Kafka, no audit consumer, no rate limiting on the quickstart config
(`configs/mock_demo.yaml`).

Wait until logs show the gateway is up (or ~30s after build).

## 2. Send a request

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
```

You should get **HTTP 200** and JSON with a model reply. Response headers include:

| Header | Meaning |
|--------|---------|
| `x-request-id` | Meridian request id (starts with `mrdn-`) |
| `x-meridian-backend` | Which mock served the request (`fast` or `slow`) |

Streaming:

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Count to 5"}],"stream":true}'
```

Ends with `data: [DONE]`.

## 3. Operator views

```bash
# Backend health
curl -s http://localhost:8080/meridian/status | python3 -m json.tool

# Metrics
curl -s http://localhost:8080/metrics | head
```

Dashboard: open **http://localhost:8080/ui**

## 4. Optional smoke script

```bash
# from host with Python + deps, against the running compose stack
pip install -e ".[dev]"   # once
python scripts/smoke_test.py --url http://localhost:8080 --model demo-model
```

## 5. Stop

```bash
docker compose down
```

## Next steps

| Want | Read |
|------|------|
| Real GPU (Ollama) | [`LOAD.md`](./LOAD.md) recipe or `configs/local_gpu.yaml` |
| Production deploy | [`DEPLOY.md`](./DEPLOY.md) |
| Auth / budgets / cost | [`ENTERPRISE_COST.md`](./ENTERPRISE_COST.md), `configs/enterprise_example.yaml` |
| Full config reference | [`CONFIGURATION.md`](./CONFIGURATION.md) |
| Docs map | [`README.md`](./README.md) (this folder) |

**Env var:** always `MERIDIAN_CONFIG` (uppercase), e.g.  
`MERIDIAN_CONFIG=configs/local_gpu.yaml uvicorn meridian.api.main:app --port 8080`
