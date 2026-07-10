"""Shared helpers for in-process mock backends.

Always use stdlib asyncio (not uvloop) so process exit does not SIGSEGV when
daemon uvicorn threads are still shutting down (GitHub Actions / py3.11).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import uvicorn


def start_mock_server(app: Any, host: str, port: int) -> uvicorn.Server:
    """Start uvicorn in a daemon thread; return the Server for optional shutdown."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
        loop="asyncio",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name=f"mock-uvicorn-{port}")
    thread.start()
    deadline = time.monotonic() + 10.0
    url = f"http://{host}:{port}/v1/models"
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=0.5)
            return server
        except Exception as exc:  # noqa: BLE001 — readiness
            last_err = exc
            time.sleep(0.05)
    raise RuntimeError(f"mock not up on {host}:{port}: {last_err}")


def stop_server(server: uvicorn.Server | None) -> None:
    if server is None:
        return
    server.should_exit = True
