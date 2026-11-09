"""Generate realistic mixed traffic through a running Meridian gateway.

Use it to fill the dashboards (gateway console and Meridian Cloud) with
something to look at: request log, latency percentiles, status classes,
streaming vs non-streaming, per-model counts, session stickiness, workload
tiers and, when keys are given, per-org usage and cost.

The mix, per request:
  - ~55% short chats, ~20% long generations, ~10% long prompts
  - ~35% streamed (SSE), the rest non-streamed
  - ~30% carry an x-meridian-session header (a few sticky "conversations")
  - ~7% deliberately bad, so error views have rows: an unknown model (the
    gateway answers 503, no backend serves it) or a malformed body (passed
    through; real servers answer 400, the demo's mock backends 500)

Standard library only; runs anywhere Python 3.11+ does:

    python scripts/gen_traffic.py                          # 5 minutes at ~2 req/s
    python scripts/gen_traffic.py --rps 5 --duration 600 --concurrency 8
    python scripts/gen_traffic.py --key mrdn_... --key mrdn_...   # rotate tenant keys
    python scripts/gen_traffic.py --url http://gpu-box:8080 --model qwen2.5:0.5b

Models default to everything GET /v1/models lists. Prompts are generated
filler; nothing personal is sent. Ctrl-C stops it and prints a summary
(in-flight requests finish first; long generations can take a while).
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

TOPICS = [
    "how a load balancer chooses a backend",
    "why GPUs are good at matrix multiplication",
    "the difference between latency and throughput",
    "what a KV cache stores during decoding",
    "how health checks detect a failing server",
    "why streaming responses feel faster",
    "what continuous batching does",
    "how rate limits protect shared services",
]

SHORT = [
    "In one sentence, explain {t}.",
    "Give three keywords for {t}.",
    "Is this true or false: {t} matters for chat apps?",
]
LONG_GEN = [
    "Write a detailed, step-by-step explanation of {t}, with an example.",
    "Write a short story that teaches {t}.",
]
FILLER = "Context paragraph: inference fleets route requests across many model servers and must stay reliable. "


def build_request(models: list[str], sessions: list[str], rng: random.Random) -> tuple[str, dict, dict[str, str]]:
    """Return (kind, body, extra headers) for one request."""
    t = rng.choice(TOPICS)
    model = rng.choice(models)
    headers: dict[str, str] = {}
    roll = rng.random()
    if roll < 0.04:
        return (
            "bad-model",
            {"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
            headers,
        )
    if roll < 0.07:
        return "bad-body", {"model": model, "messages": "not-a-list"}, headers
    if roll < 0.62:
        kind, content, max_tokens = "short", rng.choice(SHORT).format(t=t), rng.choice([16, 32, 48])
    elif roll < 0.82:
        kind, content, max_tokens = "long-gen", rng.choice(LONG_GEN).format(t=t), rng.choice([256, 384, 512])
    else:
        kind, content, max_tokens = (
            "long-prompt",
            FILLER * rng.randint(40, 90) + f"\nSummarise the above, then explain {t}.",
            64,
        )
    if rng.random() < 0.30:
        headers["x-meridian-session"] = rng.choice(sessions)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "stream": rng.random() < 0.35,
    }
    return kind, body, headers


def send(url: str, key: str | None, body: dict, extra: dict[str, str], timeout: float) -> tuple[int, float, str]:
    """POST one chat completion; returns (status, seconds, backend)."""
    headers = {"Content-Type": "application/json", **extra}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{url}/v1/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            backend = resp.headers.get("x-meridian-backend", "?")
            # Read to the end so streamed requests are measured (and finalised) fully.
            while resp.read(8192):
                pass
            return resp.status, time.monotonic() - start, backend
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, time.monotonic() - start, e.headers.get("x-meridian-backend", "-")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, time.monotonic() - start, type(e).__name__


def list_models(url: str, key: str | None) -> list[str]:
    req = urllib.request.Request(f"{url}/v1/models", headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return [m["id"] for m in json.load(resp).get("data", [])]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8080", help="gateway base URL")
    p.add_argument("--rps", type=float, default=2.0, help="target requests per second")
    p.add_argument("--duration", type=float, default=300, help="seconds to run (0 = until Ctrl-C)")
    p.add_argument("--concurrency", type=int, default=6, help="max requests in flight")
    p.add_argument("--model", action="append", help="model to use (repeatable); default: all from /v1/models")
    p.add_argument("--key", action="append", help="gateway API key (repeatable, rotated per request)")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--seed", type=int)
    a = p.parse_args()

    url = a.url.rstrip("/")
    keys = a.key or [None]
    try:
        models = a.model or list_models(url, keys[0])
    except Exception as e:  # noqa: BLE001 - report any reachability problem plainly
        print(
            f"Can't list models at {url}/v1/models: {e}\n"
            "Is the gateway running? Pass --model if /v1/models needs a key.",
            file=sys.stderr,
        )
        return 1
    if not models:
        print("The gateway lists no models; pass --model.", file=sys.stderr)
        return 1

    rng = random.Random(a.seed)
    sessions = [f"demo-conv-{i}" for i in range(6)]
    key_cycle = itertools.cycle(keys)
    stats: Counter[str] = Counter()
    by_backend: Counter[str] = Counter()
    latencies: list[float] = []
    lock = threading.Lock()
    slots = threading.Semaphore(a.concurrency)

    def one(kind: str, body: dict, extra: dict[str, str], key: str | None) -> None:
        try:
            status, secs, backend = send(url, key, body, extra, a.timeout)
            with lock:
                stats[f"{status // 100}xx" if status else "conn-error"] += 1
                stats[kind] += 1
                by_backend[backend] += 1
                if 200 <= status < 300:
                    latencies.append(secs)
        finally:
            slots.release()

    print(f"Sending ~{a.rps} req/s to {url} (models: {', '.join(models)}). Ctrl-C to stop.")
    start = time.monotonic()
    last_report = start
    sent = 0
    try:
        with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
            while not a.duration or time.monotonic() - start < a.duration:
                # Poisson arrivals look like real users; bursts exercise in-flight and queueing views.
                time.sleep(rng.expovariate(a.rps))
                if not slots.acquire(timeout=a.timeout):
                    continue
                kind, body, extra = build_request(models, sessions, rng)
                pool.submit(one, kind, body, extra, next(key_cycle))
                sent += 1
                if time.monotonic() - last_report >= 10:
                    last_report = time.monotonic()
                    with lock:
                        ok = sorted(latencies)
                        p50 = ok[len(ok) // 2] if ok else 0
                        counts = " ".join(f"{k}={stats[k]}" for k in ("2xx", "4xx", "5xx", "conn-error"))
                        print(f"  {sent} sent  {counts}  p50={p50:.2f}s")
    except KeyboardInterrupt:
        print("\nStopping…")

    ok = sorted(latencies)
    pct = lambda q: ok[min(len(ok) - 1, int(q * len(ok)))] if ok else 0.0  # noqa: E731
    print(f"\nSent {sent} requests in {time.monotonic() - start:.0f}s")
    print("  status:  " + "  ".join(f"{k}={stats[k]}" for k in ("2xx", "4xx", "5xx", "conn-error") if stats[k]))
    print(
        "  shape:   "
        + "  ".join(
            f"{k}={stats[k]}" for k in ("short", "long-gen", "long-prompt", "bad-model", "bad-body") if stats[k]
        )
    )
    print("  backend: " + "  ".join(f"{k}={v}" for k, v in by_backend.most_common()))
    print(f"  latency (2xx): p50={pct(0.5):.2f}s  p95={pct(0.95):.2f}s  max={ok[-1] if ok else 0:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
