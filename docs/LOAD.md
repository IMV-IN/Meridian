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

## Real backend path

With Ollama (or any OpenAI-compatible server) and Meridian already running:

```bash
python scripts/bench_overhead.py \
  --backend-url http://127.0.0.1:11434 \
  --gateway-url http://127.0.0.1:8080 \
  --model qwen2.5:0.5b \
  --auth mrdn_... \   # if auth.enabled
  --requests 50 --concurrency 5
```

> Real-model numbers are dominated by the engine. Use them to confirm gateway
> overhead stays small relative to generation time, not to size GPUs.

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
