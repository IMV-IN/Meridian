"""End-to-end smoke test for a running Meridian gateway.

Hits a live gateway and asserts:
  - GET  /v1/models                    returns 200 with at least one model
  - POST /v1/chat/completions (sync)   returns 200 + x-request-id + x-meridian-backend
  - POST /v1/chat/completions (stream) returns 200 + same headers + ends with [DONE]
  - Optional: /meridian/version, /meridian/status
  - Optional auth: Bearer on /v1/*
  - Optional budget remaining headers when --check-budget-headers

Usage:
    python scripts/smoke_test.py [--url http://localhost:8080] [--model demo-model]
    python scripts/smoke_test.py --auth mrdn_... --check-budget-headers

Exits 0 on success, 1 on any failure. Designed to be safe to run in CI and locally.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

REQUIRED_HEADERS = ("x-request-id", "x-meridian-backend")


def _headers(auth: Optional[str]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = f"Bearer {auth}"
    return h


def _get(
    url: str, timeout: float, auth: Optional[str] = None
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method="GET", headers=_headers(auth) if auth else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()


def _post(
    url: str,
    body: dict,
    timeout: float,
    stream: bool = False,
    auth: Optional[str] = None,
) -> tuple[int, dict[str, str], object]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=_headers(auth),
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    headers = {k.lower(): v for k, v in resp.headers.items()}
    if stream:
        return resp.status, headers, resp
    return resp.status, headers, resp.read()


def wait_for_ready(base_url: str, timeout_s: float = 30.0, auth: Optional[str] = None) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # Status is always open (ops plane)
            status, _, _ = _get(f"{base_url}/meridian/status", timeout=2.0)
            if status == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
        time.sleep(1.0)
    raise RuntimeError(f"gateway not ready at {base_url} after {timeout_s}s: {last_err}")


def check_ops(base_url: str) -> None:
    status, _, body = _get(f"{base_url}/meridian/status", timeout=5.0)
    assert status == 200, f"/meridian/status status={status}"
    payload = json.loads(body)
    assert "backends" in payload, f"/meridian/status missing backends: {payload}"
    print(f"  OK  /meridian/status -> {len(payload.get('backends') or [])} backend(s)")

    try:
        status, _, body = _get(f"{base_url}/meridian/version", timeout=5.0)
        if status == 200:
            ver = json.loads(body).get("version", "?")
            print(f"  OK  /meridian/version -> {ver}")
    except Exception:
        print("  --  /meridian/version not available (older build)")


def check_models(base_url: str, auth: Optional[str]) -> None:
    status, _, body = _get(f"{base_url}/v1/models", timeout=5.0, auth=auth)
    assert status == 200, f"/v1/models status={status}"
    payload = json.loads(body)
    data = payload.get("data") or []
    assert isinstance(data, list) and len(data) >= 1, f"/v1/models empty: {payload}"
    print(f"  OK  /v1/models -> {len(data)} model(s)")


def check_required_headers(headers: dict[str, str], where: str) -> None:
    for h in REQUIRED_HEADERS:
        assert h in headers and headers[h], f"{where}: missing header {h!r} in {list(headers)}"


def check_budget_headers(headers: dict[str, str], where: str) -> None:
    for h in (
        "x-meridian-budget-remaining-tokens",
        "x-meridian-budget-remaining-requests",
    ):
        assert h in headers and headers[h] != "", f"{where}: missing {h}"


def check_non_stream(
    base_url: str,
    model: str,
    auth: Optional[str],
    require_budget: bool,
) -> None:
    status, headers, body = _post(
        f"{base_url}/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": "smoke test"}]},
        timeout=30.0,
        auth=auth,
    )
    assert status == 200, f"non-stream status={status}"
    check_required_headers(headers, "non-stream")
    if require_budget:
        check_budget_headers(headers, "non-stream")
    payload = json.loads(body)
    choices = payload.get("choices") or []
    assert choices, f"non-stream: no choices in {payload}"
    print(
        f"  OK  non-stream -> backend={headers['x-meridian-backend']} "
        f"req={headers['x-request-id']}"
    )


def check_stream(
    base_url: str,
    model: str,
    auth: Optional[str],
    require_budget: bool,
) -> None:
    status, headers, resp = _post(
        f"{base_url}/v1/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": "smoke test stream"}],
            "stream": True,
        },
        timeout=30.0,
        stream=True,
        auth=auth,
    )
    assert status == 200, f"stream status={status}"
    check_required_headers(headers, "stream")
    if require_budget:
        check_budget_headers(headers, "stream")

    saw_done = False
    chunks = 0
    for line in resp:
        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            continue
        chunks += 1
        if decoded == "data: [DONE]":
            saw_done = True
            break
    resp.close()
    assert chunks > 0, "stream: received no chunks"
    assert saw_done, "stream: did not see [DONE] terminator"
    print(
        f"  OK  stream     -> backend={headers['x-meridian-backend']} "
        f"chunks={chunks}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Meridian smoke test")
    p.add_argument("--url", default="http://localhost:8080", help="Gateway base URL")
    p.add_argument("--model", default="demo-model", help="Model name for chat requests")
    p.add_argument(
        "--wait",
        type=float,
        default=30.0,
        help="Seconds to wait for gateway readiness (0 to skip)",
    )
    p.add_argument("--auth", default=None, help="Bearer API key for /v1/* when auth is on")
    p.add_argument(
        "--check-budget-headers",
        action="store_true",
        help="Require x-meridian-budget-remaining-* on chat responses",
    )
    args = p.parse_args()

    base = args.url.rstrip("/")
    print(f"Smoke testing Meridian at {base}")

    try:
        if args.wait > 0:
            wait_for_ready(base, timeout_s=args.wait, auth=args.auth)
        check_ops(base)
        check_models(base, args.auth)
        check_non_stream(base, args.model, args.auth, args.check_budget_headers)
        check_stream(base, args.model, args.auth, args.check_budget_headers)
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
