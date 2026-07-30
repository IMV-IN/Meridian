# Quickstart — Meridian in scenarios

Each scenario below is copy-paste runnable and states **what you should see**.
Together they are the **0.9.4 manual-validation recipe** — run A–F against the
mock stack before tagging; G needs a GPU/Ollama and is optional.

**You need:** Docker + Docker Compose, free ports **8080**, **9001**, **9002**.
Scenarios C–F also use `jq` (or swap in `python3 -m json.tool`).

| # | Scenario | Config | GPU |
|---|----------|--------|-----|
| A | Non-stream chat | `mock_demo.yaml` (default) | no |
| B | Streaming SSE | `mock_demo.yaml` | no |
| C | Auth + RBAC roles | `rbac_demo.yaml` | no |
| D | Budget 429 | `rbac_demo.yaml` | no |
| E | Cost / usage export | `rbac_demo.yaml` | no |
| F | Telemetry & tiering headers | `mock_demo.yaml` | no |
| G | Real backend (Ollama) | `local_gpu.yaml` | yes |
| ★ | **Phase 3 flagship: isolation, key lifecycle API, canary auto-rollback** | `phase3_demo.yaml` | no |

**New here? Skip the table and run the flagship:** `scripts/demo_phase3.sh`
brings up its own stack (port 8181) and walks every headline feature with
expected output inline.

---

## Start the mock stack (Scenarios A, B, F)

```bash
git clone https://github.com/IMV-IN/Meridian.git
cd Meridian
docker compose up --build
```

| Service | URL |
|---------|-----|
| Meridian gateway | http://localhost:8080 |
| Mock backend `fast` (~50 ms) | http://localhost:9001 |
| Mock backend `slow` (~300 ms) | http://localhost:9002 |

No Kafka, no audit consumer, no auth on the default config (`configs/mock_demo.yaml`).
Wait until the gateway logs it is up (~30s after build).

---

## Scenario A — non-stream chat

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello!"}]}'
```

**Expect:** HTTP 200 + JSON reply. Response headers include:

| Header | Meaning |
|--------|---------|
| `x-request-id` | Meridian request id (starts with `mrdn-`) |
| `x-meridian-backend` | Which mock served it (`fast` or `slow`) |

## Scenario B — streaming

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Count to 5"}],"stream":true}'
```

**Expect:** a sequence of `data: {...}` SSE lines ending with `data: [DONE]`.

## Dashboard (any time)

Open **http://localhost:8080/ui**. You get:

- a **stats bar** — recent request count, error rate, p50/p95 latency, backends up
- a **backend card** per backend with health, inflight, EWMA, and telemetry bars
- a **recent-requests table** with org / team / tier columns
- an **API-key control** (top right) — needed once auth is on (Scenario C)

---

## Scenario C — auth + RBAC roles

Stop the default stack (`Ctrl-C`) and bring up the gateway with the RBAC config
mounted over the default. The two mock backends still run from compose.

```bash
docker compose up --build -d backend-fast backend-slow
docker compose run --rm --service-ports \
  -v "$(pwd)/configs/rbac_demo.yaml:/app/config.yaml:ro" \
  gateway
```

`configs/rbac_demo.yaml` defines five demo keys (documented in the file):

| Key | Role | May do |
|-----|------|--------|
| `mrdn_viewerviewerviewerviewer` | `viewer` | read dashboard/status/requests |
| `mrdn_operatoroperatoroperator` | `operator` | viewer + `POST /meridian/reload` |
| `mrdn_adminadminadminadminadmin` | `admin` | everything, incl. cross-org usage |
| `mrdn_financefinancefinance` | `cost_admin` bool | read all-org usage (no role) |
| `mrdn_appkeyappkeyappkeyappkey` | none | `/v1/*` only, **no** ops access |

**C1 — chat needs a key now:**

```bash
# No key -> 401
curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}]}'
# -> 401

# App key -> 200
curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer mrdn_appkeyappkeyappkeyappkey" \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}]}'
# -> 200
```

**C2 — ops endpoints enforce view rights:**

```bash
# No key -> 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/meridian/status
# -> 401

# Plain app key has no view right -> 403
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/meridian/status \
  -H "Authorization: Bearer mrdn_appkeyappkeyappkeyappkey"
# -> 403

# Viewer key -> 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/meridian/status \
  -H "Authorization: Bearer mrdn_viewerviewerviewerviewer"
# -> 200
```

**C3 — reload requires operator/admin:**

```bash
# Viewer cannot reload -> 403
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/meridian/reload \
  -H "Authorization: Bearer mrdn_viewerviewerviewerviewer"
# -> 403

# Operator can -> 200 {"reloaded": true, ...}
curl -s -X POST http://localhost:8080/meridian/reload \
  -H "Authorization: Bearer mrdn_operatoroperatoroperator"
# -> {"reloaded": true, "keys": 5}
```

**C4 — dashboard with auth on:** open http://localhost:8080/ui, click **Set API
key**, paste `mrdn_viewerviewerviewerviewer`. Cards and the request table
populate. Clearing the key shows the "Authentication required" banner.

## Scenario D — budget 429

`rbac_demo.yaml` caps org `acme` at **5 requests/day**. Fire six with the app key:

```bash
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "req $i -> %{http_code}\n" \
    http://localhost:8080/v1/chat/completions \
    -H "Authorization: Bearer mrdn_appkeyappkeyappkeyappkey" \
    -H "Content-Type: application/json" \
    -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}]}'
done
```

**Expect:** first 5 → `200`, the 6th → `429` (with a `Retry-After` header and
`"type": "rate_limit_exceeded"` in the body).

## Scenario E — cost / usage export

```bash
# Own-org view with the app key (scoped to acme automatically):
curl -s http://localhost:8080/meridian/usage \
  -H "Authorization: Bearer mrdn_appkeyappkeyappkeyappkey" | jq

# Cross-org view: admin or finance key can pass ?org=
curl -s "http://localhost:8080/meridian/usage?org=acme" \
  -H "Authorization: Bearer mrdn_adminadminadminadminadmin" | jq

# A non-admin asking for another org -> 403
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8080/meridian/usage?org=finance" \
  -H "Authorization: Bearer mrdn_appkeyappkeyappkeyappkey"
# -> 403
```

**Expect:** JSON rows with `org_id`, `model`, token counts and `cost`; CSV via
`/meridian/usage.csv`. The cross-org request from a plain key is refused.

## Scenario F — telemetry & tiering headers

Against the default mock stack, inspect the routing headers:

```bash
curl -sD - -o /dev/null http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"hi"}],"max_tokens":2048}' \
  | grep -i x-meridian
```

**Expect:** `x-meridian-backend`, and — when tiering/affinity are enabled in the
config — `x-meridian-tier` / `x-meridian-session-route`. Backend telemetry
(queue depth, GPU mem, tok/s), when a backend exposes it, shows as bars on `/ui`.
See [`CONFIGURATION.md`](./CONFIGURATION.md) to enable tiering and telemetry.

## Scenario G — real backend (Ollama, GPU)

```bash
ollama pull qwen2.5:0.5b && ollama serve
export MERIDIAN_CONFIG=configs/local_gpu.yaml
uvicorn meridian.api.main:app --host 0.0.0.0 --port 8080
```

Then repeat Scenario A/B against port 8080. Load / overhead numbers:
[`LOAD.md`](./LOAD.md).

---

## Smoke script

```bash
pip install -e ".[dev]"   # once
python scripts/smoke_test.py --url http://localhost:8080 --model demo-model
```

## Stop

```bash
docker compose down
```

## Next steps

| Want | Read |
|------|------|
| Production deploy | [`DEPLOY.md`](./DEPLOY.md) · [`OPS_RUNBOOK.md`](./OPS_RUNBOOK.md) |
| Auth / budgets / cost / roles | [`CONFIGURATION.md`](./CONFIGURATION.md), `configs/enterprise_example.yaml` |
| Docs map | [`index.md`](./index.md) |

**Env var:** always `MERIDIAN_CONFIG` (uppercase), e.g.
`MERIDIAN_CONFIG=configs/local_gpu.yaml uvicorn meridian.api.main:app --port 8080`
