# Load & overhead numbers (0.9.3)

How much latency Meridian adds in front of a backend, and how to re-measure
on your hardware for ~1k-user capacity planning.

## Quick measure (mock path, no GPU)

```bash
# From a dev install: pip install -e ".[dev]"
python scripts/bench_overhead.py --requests 200 --concurrency 20
# Machine-readable:
python scripts/bench_overhead.py --json
```

The script:

1. Starts an in-process **mock** backend with `BASE_LATENCY_MS=0`
2. Starts Meridian (ASGI) pointing at that mock
3. Bursts non-stream `POST /v1/chat/completions` **direct** vs **via Meridian**
4. Reports p50 / p95 / p99 / mean / RPS and overhead (via − direct)

## Real backend path (Ollama)

### Recipe

```bash
# 1) Backend
ollama pull qwen2.5:0.5b
ollama serve   # default http://127.0.0.1:11434

# 2) Meridian (config already points at Ollama)
MERIDIAN_CONFIG=configs/local_gpu.yaml \
  uvicorn meridian.api.main:app --host 127.0.0.1 --port 18080

# 3) Smoke (stream + non-stream + headers)
python scripts/smoke_test.py --url http://127.0.0.1:18080 --model qwen2.5:0.5b

# 4) Overhead (direct Ollama vs via Meridian)
python scripts/bench_overhead.py \
  --backend-url http://127.0.0.1:11434 \
  --gateway-url http://127.0.0.1:18080 \
  --model qwen2.5:0.5b \
  --requests 30 --concurrency 1 --warmup 3

# Optional light concurrency:
python scripts/bench_overhead.py \
  --backend-url http://127.0.0.1:11434 \
  --gateway-url http://127.0.0.1:18080 \
  --model qwen2.5:0.5b \
  --requests 20 --concurrency 4 --warmup 2
```

If `auth.enabled`, add `--auth mrdn_...` to the bench and smoke commands.

> Real-model **absolute** latency is dominated by the engine. Use these numbers
> to confirm **gateway overhead stays small relative to generation time**, not
> to size GPUs.

## Reference numbers (mock, single host)

Recorded 2026-07-10 on a Linux dev box, Python 3.11, mock `BASE_LATENCY_MS=0`,
Meridian defaults (no auth/budgets/cost on the hot path). Meridian path uses
ASGITransport (no second HTTP hop to the gateway process).

### Serial isolation (`n=100`, `concurrency=1`) — clean overhead

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS |
|------|----------|----------|-----------|-----|
| Direct → mock | 0.68 | 0.91 | 0.74 | ~1336 |
| Via Meridian | 1.35 | 1.88 | 1.44 | ~689 |
| **Overhead** | **~0.7 ms** | **~1.0 ms** | **~0.7 ms** | — |

### Concurrent load (`n=200`, `concurrency=20`) — capacity-ish

| Path | p50 (ms) | mean (ms) | RPS |
|------|----------|-----------|-----|
| Direct → mock | ~28 | ~34 | ~556 |
| Via Meridian | ~34 | ~39 | ~486 |
| **Delta p50** | **~6 ms** | — | — |

Under high concurrency, absolute ms are dominated by event-loop / socket
contention; use **serial isolation** for “how many ms does Meridian add?” and
concurrent runs for relative RPS. Re-run on your hardware:

```bash
python scripts/bench_overhead.py --requests 100 --concurrency 1
python scripts/bench_overhead.py --requests 200 --concurrency 20
```

CI runs the harness (`--requests 40`) to ensure it stays green; it does **not**
assert absolute ms (hardware variance).

## Reference numbers (Ollama, real path)

Recorded **2026-07-10** on the same Linux host:

| Host detail | Value |
|-------------|--------|
| GPU | NVIDIA GeForce RTX 4060 Laptop (8 GiB) |
| Backend | Ollama `qwen2.5:0.5b` on `127.0.0.1:11434` |
| Meridian | **v0.9.3**, `configs/local_gpu.yaml`, port **18080** (no auth/budgets/cost) |
| Request shape | non-stream chat, `max_tokens=8`, message `"bench"` |

### Serial isolation (`n=30`, `concurrency=1`)

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS | errors |
|------|----------|----------|-----------|-----|--------|
| Direct → Ollama | 151.0 | 153.2 | 151.2 | 6.6 | 0 |
| Via Meridian | 153.0 | 155.3 | 153.3 | 6.5 | 0 |
| **Overhead** | **~1.9 ms** | **~2.1 ms** | **~2.1 ms** | — | — |

**Takeaway:** gateway adds ~**2 ms** (~**1.3%** of end-to-end p50). Engine time is the budget.

### Light concurrent (`n=20`, `concurrency=4`)

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS | errors |
|------|----------|----------|-----------|-----|--------|
| Direct → Ollama | 180.7 | 243.1 | 189.1 | 20.2 | 0 |
| Via Meridian | 186.7 | 235.5 | 192.8 | 19.9 | 0 |
| **Delta p50** | **~5.9 ms** | (noisy) | — | ~same RPS | — |

Under concurrency, engine queueing dominates; Meridian RPS tracks direct within ~2%.

### Functional proof (same stack)

`scripts/smoke_test.py --url http://127.0.0.1:18080 --model qwen2.5:0.5b` — pass
(`/meridian/status`, `/meridian/version` **0.9.3**, non-stream + stream/`[DONE]`,
`x-meridian-backend=ollama-4070`).

### How to interpret for ~1000 users

| Concurrent open streams (steady) | Gateway starting point |
|----------------------------------|------------------------|
| ≤ 50 | 0.5–1 vCPU, 256–512 MiB |
| ≤ 200 | 1–2 vCPU, 512 MiB–1 GiB |
| ≤ 500 | 2–4 vCPU, 1–2 GiB; watch open sockets + JSONL I/O |

Meridian is **I/O-bound** (proxy + policy). GPU/model capacity is almost always
the bottleneck. Budget/cost sqlite and JSONL appends add disk I/O — put them on
fast local storage in production.

## Methodology notes

- **Warmup** excluded from percentiles (default 20 requests each path).
- Non-stream only in the default harness (stream first-byte latency is a
  different metric; use smoke + your load tool for TTFT).
- Auth/budgets/cost on: expect a small extra fixed cost (key lookup + meter
  transaction). Measure with your production config.
- Do not compare cross-machine numbers without noting CPU, cgroup limits, and
  whether mock sleep was zero.

## Related

- Sizing table: [`DEPLOY.md`](./DEPLOY.md)
- Smoke: `python scripts/smoke_test.py --url http://localhost:8080`
- Ops: [`OPS_RUNBOOK.md`](./OPS_RUNBOOK.md)
