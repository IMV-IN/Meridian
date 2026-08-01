#!/usr/bin/env python3
"""Validate a live OpenAI-compatible backend through a disposable Meridian process."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent


def request_json(
    url: str, *, body: Optional[dict[str, Any]] = None, timeout: float = 60.0
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        assert response.status == 200, f"{url} returned HTTP {response.status}"
        return json.loads(response.read())


def check_direct_backend(base_url: str, model: str) -> None:
    models = request_json(f"{base_url}/v1/models", timeout=10)
    assert models.get("data"), f"backend returned no models: {models}"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 8,
    }
    completion = request_json(f"{base_url}/v1/chat/completions", body=body)
    assert completion.get("choices"), f"backend returned no choices: {completion}"

    stream_body = {**body, "stream": True}
    data = json.dumps(stream_body).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    saw_done = False
    with urllib.request.urlopen(request, timeout=60) as response:
        for line in response:
            if line.decode(errors="replace").strip() == "data: [DONE]":
                saw_done = True
                break
    assert saw_done, "backend stream did not end with data: [DONE]"


def wait_for_gateway(url: str, process: subprocess.Popen[str], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Meridian exited before readiness with status {process.returncode}")
        try:
            request_json(f"{url}/meridian/status", timeout=2)
            return
        except (OSError, ValueError, AssertionError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError(f"Meridian was not ready at {url} after {timeout:.0f}s")


def command_output(command: list[str]) -> Optional[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def gpu_metadata() -> Optional[dict[str, str]]:
    output = command_output([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if not output:
        return None
    name, memory_mib, driver = (part.strip() for part in output.splitlines()[0].split(",", 2))
    return {"name": name, "memory_mib": memory_mib, "driver": driver}


def run_checked(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), file=sys.stderr)
    return subprocess.run(command, check=True, text=True, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--engine-version", default="unknown")
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision")
    parser.add_argument("--gateway-port", type=int, default=18080)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    backend_url = args.backend_url.rstrip("/")
    gateway_url = f"http://127.0.0.1:{args.gateway_port}"
    check_direct_backend(backend_url, args.model)

    with tempfile.TemporaryDirectory(prefix="meridian-real-backend-") as directory:
        workdir = Path(directory)
        config_path = workdir / "config.yaml"
        log_path = workdir / "meridian.log"
        config = {
            "gateway": {"host": "127.0.0.1", "port": args.gateway_port, "strategy": "least_inflight"},
            "health": {"interval_s": 5, "timeout_s": 2, "fail_threshold": 2, "success_threshold": 1},
            "logging": {"level": "WARNING", "jsonl_path": str(workdir / "requests.jsonl")},
            "backends": [{
                "name": f"{args.engine}-validation",
                "url": backend_url,
                "engine": args.engine,
                "model": args.model,
                "weight": 1,
                "health_endpoint": "/v1/models",
            }],
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        environment = {**os.environ, "MERIDIAN_CONFIG": str(config_path)}

        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "meridian.api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.gateway_port),
                ],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                wait_for_gateway(gateway_url, process)
                run_checked([
                    sys.executable,
                    str(ROOT / "scripts/smoke_test.py"),
                    "--url",
                    gateway_url,
                    "--model",
                    args.model,
                    "--wait",
                    "0",
                ])
                benchmark = run_checked([
                    sys.executable,
                    str(ROOT / "scripts/bench_overhead.py"),
                    "--backend-url",
                    backend_url,
                    "--gateway-url",
                    gateway_url,
                    "--model",
                    args.model,
                    "--requests",
                    str(args.requests),
                    "--concurrency",
                    str(args.concurrency),
                    "--warmup",
                    str(args.warmup),
                    "--json",
                ], capture_output=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        if process.returncode not in (0, -15):
            raise RuntimeError(f"Meridian exited with status {process.returncode}; log: {log_path.read_text()}")

    evidence = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "meridian_version": command_output([sys.executable, "-c", "import meridian; print(meridian.__version__)"]),
        "engine": args.engine,
        "engine_version": args.engine_version,
        "backend_url": backend_url,
        "model": args.model,
        "model_revision": args.model_revision,
        "host": {"platform": platform.platform(), "python": platform.python_version(), "gpu": gpu_metadata()},
        "checks": {
            "direct_models": "passed",
            "direct_chat": "passed",
            "direct_stream": "passed",
            "gateway_smoke": "passed",
        },
        "benchmark": json.loads(benchmark.stdout),
    }
    rendered = json.dumps(evidence, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Evidence written to {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
