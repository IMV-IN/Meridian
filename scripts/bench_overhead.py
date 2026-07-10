#!/usr/bin/env python3
"""Measure Meridian gateway overhead vs direct-to-backend (mock path).

Starts an in-process mock backend and Meridian ASGI app, then reports
latency percentiles and throughput for:
  - direct → mock backend
  - via Meridian → mock backend

Usage:
    python scripts/bench_overhead.py [--requests 200] [--concurrency 20]
    python scripts/bench_overhead.py --json   # machine-readable summary

No GPU required. Mock backend uses BASE_LATENCY_MS=0 so numbers reflect
proxy/policy overhead rather than synthetic engine delay.

For a real-backend path, point --backend-url at a live OpenAI-compatible
server and --gateway-url at a running Meridian (both must already be up);
then only the client-side comparison runs (no in-process servers).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import List, Optional, Sequence, Tuple

import httpx
import uvicorn

# Repo root on path for local runs
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@dataclass
class Latencies:
    n: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    rps: float
    errors: int


def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(samples_ms: List[float], errors: int, wall_s: float) -> Latencies:
    ok = sorted(samples_ms)
    n = len(ok)
    rps = n / wall_s if wall_s > 0 else 0.0
    return Latencies(
        n=n,
        p50_ms=_percentile(ok, 50) if ok else 0.0,
        p95_ms=_percentile(ok, 95) if ok else 0.0,
        p99_ms=_percentile(ok, 99) if ok else 0.0,
        mean_ms=statistics.fmean(ok) if ok else 0.0,
        rps=rps,
        errors=errors,
    )


def _find_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_uvicorn(app, host: str, port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        app, host=host, port=port, log_level="error", loop="asyncio", lifespan="off"
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    return server


async def _wait_http(url: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    last: Optional[Exception] = None
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url, timeout=1.0)
                if r.status_code < 500:
                    return
            except Exception as exc:  # noqa: BLE001 — readiness probe
                last = exc
            await asyncio.sleep(0.05)
    raise RuntimeError(f"not ready: {url} ({last})")


async def _burst(
    url: str,
    *,
    n: int,
    concurrency: int,
    body: dict,
    headers: Optional[dict] = None,
) -> Tuple[List[float], int, float]:
    sem = asyncio.Semaphore(concurrency)
    samples: List[float] = []
    errors = 0
    lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def one() -> None:
            nonlocal errors
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.post(url, json=body, headers=headers or {})
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    if r.status_code >= 400:
                        async with lock:
                            errors += 1
                    else:
                        async with lock:
                            samples.append(elapsed)
                except Exception:
                    async with lock:
                        errors += 1

        wall0 = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(n)])
        wall = time.perf_counter() - wall0
    return samples, errors, wall


async def _burst_asgi(
    app,
    path: str,
    *,
    n: int,
    concurrency: int,
    body: dict,
) -> Tuple[List[float], int, float]:
    """Burst against an ASGI app (avoids uvicorn lifespan/signal on threads)."""
    sem = asyncio.Semaphore(concurrency)
    samples: List[float] = []
    errors = 0
    lock = asyncio.Lock()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://bench", timeout=30.0) as client:

        async def one() -> None:
            nonlocal errors
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.post(path, json=body)
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    if r.status_code >= 400:
                        async with lock:
                            errors += 1
                    else:
                        async with lock:
                            samples.append(elapsed)
                except Exception:
                    async with lock:
                        errors += 1

        wall0 = time.perf_counter()
        await asyncio.gather(*[one() for _ in range(n)])
        wall = time.perf_counter() - wall0
    return samples, errors, wall


async def run_inprocess(
    n: int,
    concurrency: int,
    warmup: int,
) -> dict:
    os.environ["BASE_LATENCY_MS"] = "0"
    os.environ["BACKEND_NAME"] = "bench-mock"
    os.environ["MODEL_NAME"] = "demo-model"

    from mock_backend.server import app as mock_app

    mock_port = _find_free_port()
    _start_uvicorn(mock_app, "127.0.0.1", mock_port)
    mock_url = f"http://127.0.0.1:{mock_port}"
    await _wait_http(f"{mock_url}/v1/models")

    from meridian.api.main import app as meridian_app
    from meridian.api.main import init_app
    from meridian.config.models import MeridianConfig

    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight", "default_max_tokens": 16},
        "health": {"interval_s": 3600, "timeout_s": 2, "fail_threshold": 3, "success_threshold": 1},
        "logging": {"level": "WARNING"},
        "backends": [{
            "name": "bench",
            "url": mock_url,
            "engine": "mock",
            "model": "demo-model",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    # init_app without uvicorn lifespan (no SIGHUP on worker thread)
    await init_app(cfg, start_health=False)

    body = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "bench"}],
        "max_tokens": 8,
        "stream": False,
    }
    chat_path = "/v1/chat/completions"

    # Warmup
    if warmup > 0:
        await _burst(mock_url + chat_path, n=warmup, concurrency=min(4, concurrency), body=body)
        await _burst_asgi(
            meridian_app, chat_path, n=warmup, concurrency=min(4, concurrency), body=body
        )

    d_samples, d_err, d_wall = await _burst(
        mock_url + chat_path, n=n, concurrency=concurrency, body=body
    )
    g_samples, g_err, g_wall = await _burst_asgi(
        meridian_app, chat_path, n=n, concurrency=concurrency, body=body
    )

    direct = summarize(d_samples, d_err, d_wall)
    via = summarize(g_samples, g_err, g_wall)
    overhead_p50 = via.p50_ms - direct.p50_ms
    overhead_p95 = via.p95_ms - direct.p95_ms
    return {
        "mode": "inprocess_mock",
        "requests": n,
        "concurrency": concurrency,
        "backend_latency_ms_config": 0,
        "direct": asdict(direct),
        "via_meridian": asdict(via),
        "overhead_p50_ms": round(overhead_p50, 3),
        "overhead_p95_ms": round(overhead_p95, 3),
        "notes": (
            "Mock BASE_LATENCY_MS=0. Meridian via ASGITransport (no extra HTTP hop). "
            "Single-host, best-effort — re-run on your hardware for capacity planning."
        ),
    }


async def run_external(
    backend_url: str,
    gateway_url: str,
    n: int,
    concurrency: int,
    warmup: int,
    model: str,
    auth: Optional[str],
) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "bench"}],
        "max_tokens": 8,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {auth}"} if auth else None
    chat = "/v1/chat/completions"
    b = backend_url.rstrip("/")
    g = gateway_url.rstrip("/")

    if warmup > 0:
        await _burst(b + chat, n=warmup, concurrency=min(4, concurrency), body=body, headers=headers)
        await _burst(g + chat, n=warmup, concurrency=min(4, concurrency), body=body, headers=headers)

    d_samples, d_err, d_wall = await _burst(
        b + chat, n=n, concurrency=concurrency, body=body, headers=headers
    )
    g_samples, g_err, g_wall = await _burst(
        g + chat, n=n, concurrency=concurrency, body=body, headers=headers
    )
    direct = summarize(d_samples, d_err, d_wall)
    via = summarize(g_samples, g_err, g_wall)
    return {
        "mode": "external",
        "backend_url": b,
        "gateway_url": g,
        "requests": n,
        "concurrency": concurrency,
        "direct": asdict(direct),
        "via_meridian": asdict(via),
        "overhead_p50_ms": round(via.p50_ms - direct.p50_ms, 3),
        "overhead_p95_ms": round(via.p95_ms - direct.p95_ms, 3),
    }


def _print_human(result: dict) -> None:
    d, v = result["direct"], result["via_meridian"]
    print("Meridian overhead benchmark")
    print(f"  mode={result['mode']}  n={result['requests']}  concurrency={result['concurrency']}")
    print()
    print(f"{'':16} {'p50':>8} {'p95':>8} {'p99':>8} {'mean':>8} {'rps':>8} {'err':>5}")
    print(
        f"{'direct':16} {d['p50_ms']:8.2f} {d['p95_ms']:8.2f} {d['p99_ms']:8.2f} "
        f"{d['mean_ms']:8.2f} {d['rps']:8.1f} {d['errors']:5d}"
    )
    print(
        f"{'via_meridian':16} {v['p50_ms']:8.2f} {v['p95_ms']:8.2f} {v['p99_ms']:8.2f} "
        f"{v['mean_ms']:8.2f} {v['rps']:8.1f} {v['errors']:5d}"
    )
    print()
    print(f"  overhead p50: {result['overhead_p50_ms']:.2f} ms")
    print(f"  overhead p95: {result['overhead_p95_ms']:.2f} ms")
    if "notes" in result:
        print(f"  note: {result['notes']}")


def main() -> int:
    p = argparse.ArgumentParser(description="Meridian vs direct backend overhead")
    p.add_argument("--requests", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.add_argument("--backend-url", default=None, help="External backend base URL")
    p.add_argument("--gateway-url", default=None, help="External Meridian base URL")
    p.add_argument("--model", default="demo-model")
    p.add_argument("--auth", default=None, help="Bearer token for both paths if required")
    args = p.parse_args()

    if (args.backend_url is None) ^ (args.gateway_url is None):
        print("Provide both --backend-url and --gateway-url, or neither.", file=sys.stderr)
        return 2

    if args.backend_url:
        result = asyncio.run(
            run_external(
                args.backend_url,
                args.gateway_url,
                args.requests,
                args.concurrency,
                args.warmup,
                args.model,
                args.auth,
            )
        )
    else:
        result = asyncio.run(run_inprocess(args.requests, args.concurrency, args.warmup))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)
    return 0 if result["direct"]["errors"] == 0 and result["via_meridian"]["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
