# Load and overhead numbers

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
PYTHON=.venv/bin/python sh scripts/validate_ollama.sh
```

The profile handles backend readiness, a disposable Meridian process, smoke
checks, and the serial overhead run. See
[`REAL_ENGINE_VALIDATION.md`](./REAL_ENGINE_VALIDATION.md) for the complete
Ollama/vLLM release matrix and environment overrides.

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

Recorded **2026-07-30**. Complete machine-readable results:
[`serial`](./validation/ollama-v0.12.0.json),
[`concurrency 4`](./validation/ollama-v0.12.0-c4.json), and
[`concurrency 8`](./validation/ollama-v0.12.0-c8.json).

| Host detail | Value |
|-------------|--------|
| GPU | NVIDIA GeForce RTX 4060 Laptop (8 GiB) |
| Backend | Ollama `qwen2.5:0.5b` on `127.0.0.1:11434` |
| Meridian | **v0.12.0**, generated validation config, port **18080** (no auth/budgets/cost) |
| Ollama | **0.31.1** |
| Python | **3.12.11** |
| NVIDIA driver | **580.159.03** |
| Request shape | non-stream chat, `max_tokens=8`, message `"bench"` |

### Serial isolation (`n=30`, `concurrency=1`)

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS | errors |
|------|----------|----------|-----------|-----|--------|
| Direct → Ollama | 174.1 | 185.0 | 173.9 | 5.75 | 0 |
| Via Meridian | 180.5 | 186.2 | 181.1 | 5.52 | 0 |
| **Overhead** | **6.4 ms** | **1.3 ms** | **7.2 ms** | — | — |

**Takeaway:** this run added **6.4 ms** at p50, about **3.7%** of direct
end-to-end p50. Engine generation remained the dominant latency component.

### Concurrent load (`n=40`, `concurrency=4`)

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS | errors |
|------|----------|----------|-----------|-----|--------|
| Direct -> Ollama | 368.5 | 400.5 | 361.9 | 10.89 | 0 |
| Via Meridian | 375.5 | 401.4 | 367.1 | 10.66 | 0 |
| **Delta / ratio** | **7.0 ms** | **0.9 ms** | **5.2 ms** | **-2.2%** | — |

### Concurrent load (`n=80`, `concurrency=8`)

| Path | p50 (ms) | p95 (ms) | mean (ms) | RPS | errors |
|------|----------|----------|-----------|-----|--------|
| Direct -> Ollama | 243.0 | 321.5 | 252.9 | 30.44 | 0 |
| Via Meridian | 247.2 | 337.8 | 257.4 | 29.92 | 0 |
| **Delta / ratio** | **4.1 ms** | **16.3 ms** | **4.6 ms** | **-1.7%** | — |

The concurrency runs show no material throughput loss through Meridian. The
concurrency-8 p95 increase is more variable than the serial and concurrency-4
runs, so it should be treated as a host and engine observation rather than a
gateway capacity limit. Repeat this matrix on the target deployment hardware
before making capacity commitments.

### Functional proof (same stack)

The v0.12.0 profile passed direct models, non-stream, and stream checks, then
passed gateway status/version/models, non-stream, stream/`[DONE]`, and required
`x-request-id` / `x-meridian-backend` header checks.

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
