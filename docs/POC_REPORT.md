# Design-partner PoC report — Meridian v0.9.3

**Status:** executed (maintainer lab) · **Date:** 2026-07-10  
**Image / package:** **v0.9.3** (`meridian.__version__` / tag `v0.9.3`)  
**Purpose:** Prove a multi-tenant enterprise path on a **real** OpenAI-compatible backend (Ollama) without inventing product features. This is evidence for the **v1.0 gate**, not a sales deck.

| Want | Document |
|------|----------|
| Gate checklist | [`V1_GATE.md`](./V1_GATE.md) |
| Load numbers | [`LOAD.md`](./LOAD.md) |
| Ops day-2 | [`OPS_RUNBOOK.md`](./OPS_RUNBOOK.md) |
| Cost authz | [`ENTERPRISE_COST.md`](./ENTERPRISE_COST.md) |
| Pitch claims | [`PITCH.md`](./PITCH.md) — only what is true on this tag |

---

## 1. Environment

| Item | Value |
|------|--------|
| Host GPU | NVIDIA GeForce RTX 4060 Laptop, 8 GiB |
| Inference | Ollama `qwen2.5:0.5b` @ `http://127.0.0.1:11434` |
| Gateway | Meridian **v0.9.3**, config `configs/poc_design_partner.yaml` |
| Listen | `http://127.0.0.1:18080` |
| Features on | auth, rate limit, budgets (memory), cost (memory) |
| Features off | PII (optional for this run), audit bus, tiering |

**Start recipe**

```bash
ollama pull qwen2.5:0.5b
ollama serve

MERIDIAN_CONFIG=configs/poc_design_partner.yaml \
  uvicorn meridian.api.main:app --host 127.0.0.1 --port 18080
```

PoC keys (dev only — pattern `mrdn_` + 20–40 alphanumeric):

| Role | Key (in config) | Identity |
|------|-----------------|----------|
| App | `mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ` | org `acme`, team `eng` |
| Cost admin | `mrdn_1Aa2Bb3Cc4Dd5Ee6Ff7Gg8Hh` | `cost_admin` |
| Ops | `mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc` | `ops_admin` |

---

## 2. Results summary

| # | Check | Result |
|---|--------|--------|
| 1 | `GET /meridian/version` | **Pass** — `version: 0.9.3` |
| 2 | `GET /meridian/status` | **Pass** — backend `ollama-poc` healthy |
| 3 | Chat without Bearer | **Pass** — **401** |
| 4 | Non-stream chat with app key | **Pass** — 200, `x-meridian-backend: ollama-poc`, budget remaining headers present |
| 5 | Stream + smoke suite | **Pass** — `scripts/smoke_test.py --auth … --check-budget-headers` |
| 6 | Cost usage own org | **Pass** — 200, rows only `acme` |
| 7 | Cost usage other org | **Pass** — **403** |
| 8 | Cost admin export | **Pass** — 200 |
| 9 | Prometheus samples | **Pass** — `meridian_requests_total`, `meridian_tokens_total{model,kind}`, no org labels |
| 10 | JSONL prompt leak | **Pass** — prompt text **absent** from `poc_meridian_requests.jsonl` |
| 11 | Load overhead (prior run) | **Pass** — ~**1.9 ms** p50 gateway overhead vs ~151 ms engine ([LOAD.md](./LOAD.md)) |

### Sample evidence (2026-07-10)

```text
GET /meridian/version  →  {"name":"meridian","version":"0.9.3",...}
GET /meridian/status   →  least_inflight [('ollama-poc', True)]

Unauthed chat          →  HTTP 401
Authed non-stream      →  HTTP 200
  x-request-id: mrdn-...
  x-meridian-backend: ollama-poc
  x-meridian-budget-remaining-tokens: 4999954
  x-meridian-budget-remaining-requests: 9999
  usage: prompt_tokens + completion_tokens present

Smoke (auth + budget headers + stream [DONE]) → All smoke checks passed.

GET /meridian/usage (app)     → 200, orgs={'acme'}
GET /meridian/usage?org=other → 403
GET /meridian/usage (admin)   → 200

JSONL last lines: request_id, org_id, status — no prompt body.
```

---

## 3. What this proves for a design partner

1. **Drop-in OpenAI path** works against a real engine (Ollama), stream + non-stream.
2. **Auth** is enforced on `/v1/*` when enabled; identity drives budgets and cost scope.
3. **Budgets** reserve pre-flight and surface remaining capacity on response headers.
4. **Cost ledger** records actual usage and is **org-scoped** for non-admins.
5. **Observability** is metadata-only (JSONL) and cardinality-safe (Prometheus).
6. **Overhead** is small vs generation time (~1–2% on this small-model serial path).

---

## 4. Explicit non-claims (do not pitch)

| Claim | Reality on v0.9.3 |
|-------|-------------------|
| Multi-cloud LLM router / LiteLLM replacement | **No** — single OpenAI-compatible fleet |
| Semantic cache / batch async API | **Not shipped** |
| True KV-cache-aware routing / P-D disaggregation | **Not shipped** |
| SSO / OIDC / enterprise RBAC | **No** — API keys only |
| Public internet exposure without edge TLS + network ACL | **Unsupported** threat model |
| Response-body PII scan | **Request path only** |
| Multi-replica shared budget state | **Single process** sqlite/memory (Redis later) |

---

## 5. Partner re-run checklist (copy/paste)

```bash
# Backend
ollama pull qwen2.5:0.5b && ollama serve

# Gateway
pip install -e ".[dev]"   # or use ghcr.io/imv-in/meridian:0.9.3
MERIDIAN_CONFIG=configs/poc_design_partner.yaml \
  uvicorn meridian.api.main:app --host 127.0.0.1 --port 18080

# Functional
python scripts/smoke_test.py \
  --url http://127.0.0.1:18080 \
  --model qwen2.5:0.5b \
  --auth mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ \
  --check-budget-headers

# Overhead (optional)
python scripts/bench_overhead.py \
  --backend-url http://127.0.0.1:11434 \
  --gateway-url http://127.0.0.1:18080 \
  --model qwen2.5:0.5b \
  --requests 30 --concurrency 1 --warmup 3
```

For production-shaped config (sqlite paths, keys_file, PII on), start from
`configs/enterprise_example.yaml` + [`DEPLOY.md`](./DEPLOY.md).

---

## 6. Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Maintainer (lab run) | — | 2026-07-10 | Sections 1–3 executed; results above |
| Design partner | | | Fill after joint re-run |
| Security review | | | See SECURITY checklist in `V1_GATE.md` |

**v1.0 tag rule:** only after [`V1_GATE.md`](./V1_GATE.md) is fully checked and pitch claims match this report.
