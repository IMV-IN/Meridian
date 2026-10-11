#!/usr/bin/env python3
"""Three-arm prefix-sharing sweep over mock or dedicated real backends.

Default: two local synthetic LRU engines. Real mode requires two --backend-url
arguments and --reset-caches: resets are destructive to cache residency, so use
dedicated experiment instances. CSV/JSON contain metadata only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from meridian.cost.extract import usage_from_sse_bytes  # noqa: E402
from scripts.bench_overhead import _percentile  # noqa: E402

ARMS = (("A", "least_inflight", False), ("B", "cache_affinity", False), ("C", "cache_affinity", True))


def workload(
    n: int, sharing_ratio: int, *, seed: int, working_set: int, prompt_chars: int, offset: int = 0,
) -> list[dict]:
    rng = random.Random(seed)
    shared = set(rng.sample(range(n), round(n * sharing_ratio / 100)))
    result = []
    for i in range(n):
        group = f"shared-{rng.randrange(working_set)}" if i in shared else f"unique-{i + offset}"
        text_rng = random.Random(f"{seed}:{group}")
        prefix = " ".join(f"word{text_rng.randrange(10000)}" for _ in range(prompt_chars // 4 + 1))
        content = prefix[:prompt_chars - 64] + f" Question {i:010d}: reply with one word."
        result.append({"messages": [{"role": "user", "content": content}], "max_tokens": 8,
                       "temperature": 0, "stream": True, "stream_options": {"include_usage": True}})
    return result


def prediction_stats(metrics: str) -> dict:
    counts = {"predicted_hit": 0, "predicted_miss": 0, "no_prediction": 0}
    for family in text_string_to_metric_families(metrics):
        for sample in family.samples:
            if sample.name == "meridian_cache_affinity_decisions_total":
                counts[sample.labels["outcome"]] = int(sample.value)
    predictions = counts["predicted_hit"] + counts["predicted_miss"]
    return {"decisions": counts, "prediction_accuracy":
            counts["predicted_hit"] / predictions if predictions else None}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def server(module: str, port: int, log: Path, env: dict, *, factory: bool = False):
    command = [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port),
               "--loop", "asyncio", "--log-level", "error", "--no-access-log"]
    if factory:
        command.append("--factory")
    with log.open("w") as handle:
        process = subprocess.Popen(command, env={**os.environ, **env}, cwd=ROOT, stdout=handle, stderr=handle)
        try:
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def ready(url: str, process: subprocess.Popen, log: Path) -> None:
    async with httpx.AsyncClient(timeout=1) as client:
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError(f"Server failed: {log.read_text()[-3000:]}")
            try:
                response = await client.get(url + "/v1/models")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.05)
    raise RuntimeError(f"Server not ready: {url}")


async def request_row(client: httpx.AsyncClient, body: dict, *, arm: str, ratio: int, repeat: int) -> dict:
    started = time.perf_counter()
    first_token = None
    tail = bytearray()
    done = False
    async with client.stream("POST", "/v1/chat/completions", json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            raw = line.encode() + b"\n"
            tail.extend(raw)
            if len(tail) > 65536:
                del tail[:-65536]
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                continue
            if not data:
                continue
            chunk = json.loads(data)
            if first_token is None and any(c.get("delta", {}).get("content") for c in chunk.get("choices", [])):
                first_token = (time.perf_counter() - started) * 1000
        usage = usage_from_sse_bytes(bytes(tail))
        if not done or first_token is None or usage is None or not usage.cache_details_available:
            raise ValueError("Incomplete SSE stream or missing cache feedback")
        return {"request_id": response.headers.get("x-request-id", ""), "arm": arm,
                "sharing_ratio": ratio, "repeat": repeat, "prompt_tokens": usage.prompt_tokens,
                "cached_tokens": usage.cached_tokens, "ttft_ms": first_token,
                "backend": response.headers.get("x-meridian-backend", "")}


async def measure(args, urls: list[str], directory: Path) -> tuple[list[dict], list[dict]]:
    rows, summaries = [], []
    for repeat in range(args.repetitions):
        for ratio in args.sharing_ratios:
            bodies = workload(args.requests, ratio, seed=args.seed + repeat,
                              working_set=args.working_set, prompt_chars=args.prompt_chars)
            warmup = workload(args.warmup, ratio, seed=args.seed + repeat, working_set=args.working_set,
                              prompt_chars=args.prompt_chars, offset=-args.warmup)
            # Rotate arm order to reduce systematic warm-state/thermal bias.
            order = list(ARMS[repeat % 3:] + ARMS[:repeat % 3])
            for arm, strategy, correction in order:
                async with httpx.AsyncClient(timeout=30) as reset_client:
                    for url in urls:
                        result = await reset_client.post(url + "/reset_prefix_cache")
                        result.raise_for_status()
                        if result.content and result.json().get("success") is False:
                            raise RuntimeError(f"Prefix cache reset failed on {url}")
                config = {"gateway": {"strategy": strategy, "cache_affinity": {
                    "feedback_correction": correction, "locality_weight": args.locality_weight}},
                    "logging": {"jsonl_path": str(directory / "requests.jsonl")},
                    "backends": [{"name": f"engine-{i}", "url": url, "model": args.model}
                                 for i, url in enumerate(urls)]}
                path = directory / "gateway.yaml"
                path.write_text(json.dumps(config))
                port = free_port()
                log = directory / "gateway.log"
                with server("meridian.api.main:app", port, log, {"MERIDIAN_CONFIG": str(path)}) as process:
                    url = f"http://127.0.0.1:{port}"
                    await ready(url, process, log)
                    async with httpx.AsyncClient(base_url=url, timeout=120) as client:
                        for body in warmup:
                            await request_row(client, {**body, "model": args.model},
                                              arm=arm, ratio=ratio, repeat=repeat)
                        before = prediction_stats((await client.get("/metrics")).text)
                        semaphore = asyncio.Semaphore(args.concurrency)
                        scheduled = time.perf_counter()

                        async def send(i, body):
                            if args.request_rate:
                                await asyncio.sleep(max(0, scheduled + i / args.request_rate - time.perf_counter()))
                            async with semaphore:
                                return await request_row(client, {**body, "model": args.model},
                                                         arm=arm, ratio=ratio, repeat=repeat)

                        batch = await asyncio.gather(*(send(i, body) for i, body in enumerate(bodies)))
                        after = prediction_stats((await client.get("/metrics")).text)
                        counts = {k: after["decisions"][k] - before["decisions"][k] for k in after["decisions"]}
                        predictions = counts["predicted_hit"] + counts["predicted_miss"]
                rows.extend(batch)
                latencies = sorted(row["ttft_ms"] for row in batch)
                prompt = sum(row["prompt_tokens"] for row in batch)
                summaries.append({"arm": arm, "sharing_ratio": ratio, "repeat": repeat,
                                  "requests": len(batch), "cache_hit_ratio":
                                  sum(row["cached_tokens"] for row in batch) / prompt if prompt else 0,
                                  "ttft_p50_ms": _percentile(latencies, 50),
                                  "ttft_p95_ms": _percentile(latencies, 95), "errors": 0,
                                  "decisions": counts, "prediction_accuracy":
                                  counts["predicted_hit"] / predictions if predictions else None})
    return rows, summaries


async def run(args) -> dict:
    from contextlib import ExitStack

    with tempfile.TemporaryDirectory(prefix="meridian-cache-bench-") as temp, ExitStack() as stack:
        directory = Path(temp)
        urls = list(args.backend_url or [])
        if not urls:
            for i in range(2):
                port = free_port()
                log = directory / f"mock-{i}.log"
                process = stack.enter_context(server("mock_backend.cache_server:from_env", port, log,
                    {"MOCK_CACHE_ENTRIES": str(args.mock_cache_entries)}, factory=True))
                url = f"http://127.0.0.1:{port}"
                await ready(url, process, log)
                urls.append(url)
        rows, summaries = await measure(args, urls, directory)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    from meridian import __version__

    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    dirty = subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0
    return {"mode": "real" if args.backend_url else "synthetic_mock", "seed": args.seed,
            "meridian_version": __version__, "git_revision": revision.stdout.strip(), "tracked_changes": dirty,
            "python": platform.python_version(), "platform": platform.platform(),
            "engine_version": args.engine_version, "model_revision": args.model_revision,
            "gpu_description": args.gpu_description,
            "settings": {k: getattr(args, k) for k in ("requests", "warmup", "concurrency", "repetitions",
                "sharing_ratios", "working_set", "prompt_chars", "locality_weight", "mock_cache_entries",
                "request_rate")},
            "model": args.model, "summaries": summaries}


def positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", action="append")
    parser.add_argument("--reset-caches", action="store_true", help="Allow resets on dedicated real engines")
    parser.add_argument("--model", default="cache-demo")
    parser.add_argument("--engine-version", default="synthetic-lru")
    parser.add_argument("--model-revision", default="unrecorded")
    parser.add_argument("--gpu-description", default="unrecorded")
    parser.add_argument("--requests", type=positive, default=50)
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--concurrency", type=positive, default=1)
    parser.add_argument("--request-rate", type=float,
                        help="Fixed offered requests/sec; concurrency caps inflight calls")
    parser.add_argument("--repetitions", type=positive, default=3)
    parser.add_argument("--sharing-ratios", type=int, nargs="+", default=[0, 25, 50, 75, 100])
    parser.add_argument("--working-set", type=positive, default=8)
    parser.add_argument("--prompt-chars", type=positive, default=2048)
    parser.add_argument("--locality-weight", type=float, default=1000)
    parser.add_argument("--mock-cache-entries", type=positive, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.warmup < 0 or args.prompt_chars < 1024 or any(not 0 <= n <= 100 for n in args.sharing_ratios):
        parser.error("warmup must be nonnegative; prompt-chars >= 1024; sharing ratios in 0..100")
    if args.backend_url and (len(args.backend_url) != 2 or not args.reset_caches):
        parser.error("real mode requires two --backend-url arguments and --reset-caches (dedicated instances)")
    if not math.isfinite(args.locality_weight) or args.locality_weight < 0:
        parser.error("locality-weight must be finite and nonnegative")
    if args.request_rate is not None and (not math.isfinite(args.request_rate) or args.request_rate <= 0):
        parser.error("request-rate must be finite and positive")
    if args.backend_url and args.engine_version == "synthetic-lru":
        parser.error("real mode requires --engine-version")
    try:
        report = asyncio.run(run(args))
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    if args.json:
        print(rendered)
    else:
        print(f"Mode: {report['mode']} (mock results are not GPU evidence)")
        print("Arm Share Repeat Hit ratio  TTFT p50  TTFT p95")
        for row in report["summaries"]:
            print(f"{row['arm']:>3} {row['sharing_ratio']:>5}% {row['repeat']:>5} "
                  f"{row['cache_hit_ratio']:>9.3f} {row['ttft_p50_ms']:>9.2f} {row['ttft_p95_ms']:>9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
