"""Tests for meridian.health.checker — active pings + passive failure detection.

The checker's HTTP client is injected per-test with an ``httpx.MockTransport``
client so no real sockets are opened.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import httpx

from meridian.config.models import BackendConfig, HealthConfig
from meridian.health.checker import HealthChecker
from meridian.registry.backend import Backend, BackendRegistry


def _backend(name: str = "b1", health_endpoint: str = "/v1/models") -> Backend:
    return Backend(
        BackendConfig(
            name=name,
            url="http://backend.test",
            model="demo-model",
            health_endpoint=health_endpoint,
        )
    )


def _registry(*backends: Backend) -> BackendRegistry:
    return BackendRegistry(list(backends))


def _config(interval_s: float = 60.0, fail_threshold: int = 2) -> HealthConfig:
    # Long interval so the background loop never fires during a test unless
    # explicitly awaited.
    return HealthConfig(
        interval_s=interval_s,
        timeout_s=2.0,
        fail_threshold=fail_threshold,
        success_threshold=1,
    )


def _checker(
    registry: BackendRegistry,
    transport: httpx.MockTransport,
    config: Optional[HealthConfig] = None,
) -> HealthChecker:
    hc = HealthChecker(registry, config or _config())
    hc._client = httpx.AsyncClient(transport=transport)
    return hc


class TestCheckBackend:
    async def test_2xx_marks_success(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "http://backend.test/v1/models"
            return httpx.Response(200, json={"ok": True})

        backend = _backend()
        hc = _checker(_registry(backend), httpx.MockTransport(handler))

        await hc._check_backend(backend)

        assert backend.consecutive_successes == 1
        assert backend.consecutive_failures == 0
        assert backend.healthy is True
        assert hc._client is not None
        await hc._client.aclose()

    async def test_4xx_counts_as_success(self) -> None:
        """4xx means the backend is alive (misconfig, not a crash)."""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "nope"})

        backend = _backend()
        hc = _checker(_registry(backend), httpx.MockTransport(handler))

        await hc._check_backend(backend)

        assert backend.consecutive_successes == 1
        assert backend.healthy is True
        assert hc._client is not None
        await hc._client.aclose()

    async def test_5xx_counts_as_failure_and_flips_after_threshold(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "overloaded"})

        backend = _backend()
        hc = _checker(_registry(backend), httpx.MockTransport(handler))

        await hc._check_backend(backend)   # failure 1/2 — still healthy
        assert backend.consecutive_failures == 1
        assert backend.healthy is True

        await hc._check_backend(backend)   # failure 2/2 — unhealthy
        assert backend.consecutive_failures == 2
        assert backend.healthy is False
        assert hc._client is not None
        await hc._client.aclose()

    async def test_connection_error_records_failure(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        backend = _backend()
        hc = _checker(_registry(backend), httpx.MockTransport(handler))
        backend.record_health_failure(1)  # simulate pre-existing failure state

        await hc._check_backend(backend)

        assert backend.consecutive_failures >= 1
        assert backend.healthy is False
        assert hc._client is not None
        await hc._client.aclose()

    async def test_custom_health_endpoint_is_used(self) -> None:
        seen: List[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        backend = _backend(health_endpoint="/healthz")
        hc = _checker(_registry(backend), httpx.MockTransport(handler))

        await hc._check_backend(backend)

        assert seen == ["http://backend.test/healthz"]
        assert hc._client is not None
        await hc._client.aclose()


class TestPassiveFailure:
    def test_check_passive_failure_marks_unhealthy_at_threshold(self) -> None:
        backend = _backend()
        hc = HealthChecker(_registry(backend), _config(fail_threshold=2))

        hc.check_passive_failure(backend)
        assert backend.healthy is True  # 1/2 failures

        hc.check_passive_failure(backend)
        assert backend.healthy is False  # 2/2 failures

    def test_passive_failure_resets_after_success(self) -> None:
        backend = _backend()
        hc = HealthChecker(_registry(backend), _config(fail_threshold=2))

        hc.check_passive_failure(backend)
        backend.record_health_success(1)
        assert backend.consecutive_failures == 0
        assert backend.healthy is True


class TestLifecycle:
    async def test_start_creates_task_and_stop_cleans_up(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        registry = _registry(_backend())
        hc = HealthChecker(registry, _config(interval_s=3600))

        await hc.start()
        # Replace the real client with a mock so nothing external is touched.
        assert hc._client is not None
        await hc._client.aclose()
        hc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        task: Optional[asyncio.Task] = hc._task  # type: ignore[type-arg]
        assert task is not None and not task.done()

        await hc.stop()

        assert task.done()
        assert hc._client is not None
        assert hc._client.is_closed

    async def test_loop_checks_all_backends(self) -> None:
        hits: List[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        b1, b2 = _backend("b1"), _backend("b2")
        hc = _checker(_registry(b1, b2), httpx.MockTransport(handler), _config(interval_s=0.01))

        # Run one loop iteration manually (loop sleeps first, then gathers).
        await asyncio.gather(*(hc._check_backend(b) for b in hc.registry.all_backends()))

        assert sorted(hits) == [
            "http://backend.test/v1/models",
            "http://backend.test/v1/models",
        ]
        assert b1.healthy and b2.healthy
        assert hc._client is not None
        await hc._client.aclose()
