"""Phase 1 resilience: circuit breaker, retry with backoff, per-backend timeouts.

Circuit logic is tested at the state-machine level with an injected clock and
at the gateway level through the proxy functions (httpx.MockTransport — no
sockets). Stream-read timeout behavior is exercised end-to-end through the
ASGI app on one test.
"""

from __future__ import annotations

import asyncio
from typing import List

import httpx
import pytest

import meridian.proxy.forward as forward
from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.api.state import build_registry, resolve_timeouts
from meridian.config.models import (
    BackendConfig,
    CircuitBreakerConfig,
    MeridianConfig,
    ResilienceConfig,
    TimeoutConfig,
    TimeoutOverride,
)
from meridian.metrics.collectors import UPSTREAM_RETRIES
from meridian.registry.backend import Backend
from meridian.resilience import CircuitBreaker, CircuitOpenError


def _backend(name: str = "b1", **overrides) -> Backend:
    cfg = BackendConfig(name=name, url="http://backend.test", model="demo-model")
    return Backend(cfg, **overrides)


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(forward, "_client", client)
    monkeypatch.setattr(forward, "_client_loop", None)
    client_returning = lambda: client  # noqa: E731
    monkeypatch.setattr(forward, "_get_or_create_client", client_returning)
    return client


# ── Circuit breaker state machine ───────────────────────────────────────


class TestCircuitBreaker:
    def test_disabled_circuit_is_none_by_default(self) -> None:
        cb = CircuitBreakerConfig()
        assert cb.enabled is False

    def test_closed_until_threshold(self) -> None:
        c = CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=3))
        assert c.allow_request()
        c.record_failure()
        assert c.allow_request()
        c.record_failure()
        assert c.allow_request()  # still closed after 2 failures
        c.record_failure()
        assert c.state == "open"
        assert not c.allow_request()

    def test_success_resets(self) -> None:
        c = CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=2))
        c.record_failure()
        c.record_success()
        assert c.state == "closed"
        assert c.consecutive_failures == 0

    def test_half_open_probe_after_open_seconds(self) -> None:
        now = [100.0]
        c = CircuitBreaker(
            CircuitBreakerConfig(enabled=True, failure_threshold=1, open_seconds=30.0),
            clock=lambda: now[0],
        )
        c.record_failure()
        assert c.state == "open"
        assert not c.allow_request()

        now[0] += 29.0
        assert not c.allow_request()  # still within window
        now[0] += 1.0
        assert c.allow_request()  # probe admitted
        assert c.state == "half_open"
        assert not c.allow_request()  # only one probe at a time

        c.record_success()  # probe succeeded → closed
        assert c.state == "closed"
        assert c.consecutive_failures == 0

    def test_half_open_failure_reopens(self) -> None:
        now = [0.0]
        c = CircuitBreaker(
            CircuitBreakerConfig(enabled=True, failure_threshold=1, open_seconds=10.0),
            clock=lambda: now[0],
        )
        c.record_failure()
        now[0] += 10.0
        assert c.allow_request()
        c.record_failure()  # probe failed
        assert c.state == "open"
        assert not c.allow_request()
        now[0] += 10.0
        assert c.allow_request()  # window restarted at probe failure
        assert c.state == "half_open"

    def test_status_dict(self) -> None:
        c = CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=2))
        c.record_failure()
        assert c.status() == {"state": "closed", "consecutive_failures": 1}


# ── Circuit gate in the proxy path ──────────────────────────────────────


class TestCircuitGateInForward:
    async def test_open_circuit_raises_before_any_upstream_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: List[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(
            circuit=CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=1))
        )
        backend.circuit.record_failure()  # opens

        with pytest.raises(CircuitOpenError):
            await forward.forward_non_stream(backend, {"model": "m"})
        assert calls == []  # no upstream call made

    async def test_failures_open_circuit_via_forward(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(
            circuit=CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=2))
        )

        with pytest.raises(httpx.ConnectError):
            await forward.forward_non_stream(backend, {"model": "m"})
        assert backend.circuit.state == "closed"
        with pytest.raises(httpx.ConnectError):
            await forward.forward_non_stream(backend, {"model": "m"})
        assert backend.circuit.state == "open"
        with pytest.raises(CircuitOpenError):
            await forward.forward_non_stream(backend, {"model": "m"})

    async def test_successful_response_records_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(
            circuit=CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=2))
        )
        backend.circuit.record_failure()
        await forward.forward_non_stream(backend, {"model": "m"})
        assert backend.circuit.consecutive_failures == 0

    async def test_upstream_5xx_records_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(
            circuit=CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=2))
        )
        resp = await forward.forward_non_stream(backend, {"model": "m"})
        assert resp.status_code == 503  # passed through, but counted
        assert backend.circuit.consecutive_failures == 1


# ── Retry with backoff ──────────────────────────────────────────────────


class TestRetryWithBackoff:
    async def test_no_retry_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("refused")

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        with pytest.raises(httpx.ConnectError):
            await forward.forward_non_stream(_backend(), {"model": "m"})
        assert attempts == 1

    async def test_retries_until_success_and_counts_metric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0
        delays: List[float] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json={"ok": True})

        async def fake_sleep(s: float) -> None:
            delays.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        before = UPSTREAM_RETRIES.labels(backend="b1")._value.get()
        resp = await forward.forward_non_stream(
            _backend(),
            {"model": "m"},
            ResilienceConfig(max_retries=3, retry_backoff_base=0.1),
        )
        assert resp.status_code == 200
        assert attempts == 3
        assert delays == [0.1, 0.2]  # exponential backoff
        assert UPSTREAM_RETRIES.labels(backend="b1")._value.get() == before + 2

    async def test_retries_exhausted_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("refused")

        async def fake_sleep(s: float) -> None:
            pass

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        with pytest.raises(httpx.ConnectError):
            await forward.forward_non_stream(
                _backend(),
                {"model": "m"},
                ResilienceConfig(max_retries=1, retry_backoff_base=0.01),
            )
        assert attempts == 2  # initial + 1 retry

    async def test_no_retry_on_http_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, json={"error": "boom"})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        resp = await forward.forward_non_stream(
            _backend(), {"model": "m"}, ResilienceConfig(max_retries=3)
        )
        assert resp.status_code == 500  # 5xx passed through, not retried
        assert attempts == 1


# ── Timeouts ────────────────────────────────────────────────────────────


class TestTimeouts:
    def test_defaults_preserve_current_behavior(self) -> None:
        t = TimeoutConfig()
        assert (t.connect, t.read, t.write, t.pool) == (5.0, 300.0, 5.0, 5.0)
        assert t.stream_read is None

    def test_resolve_timeouts_merges_override(self) -> None:
        g = TimeoutConfig(read=120.0)
        o = TimeoutOverride(read=10.0, stream_read=1.5)
        merged = resolve_timeouts(g, o)
        assert merged.read == 10.0
        assert merged.stream_read == 1.5
        assert merged.connect == g.connect  # inherited

        # None override → global unchanged
        assert resolve_timeouts(g, None) is g

    def test_stream_read_defaults_to_read(self) -> None:
        b = _backend(timeouts=TimeoutConfig(read=42.0))
        assert forward._request_timeout(b, stream=True).read == 42.0

    def test_stream_read_override_applies_to_streams_only(self) -> None:
        b = _backend(timeouts=TimeoutConfig(read=42.0, stream_read=7.0))
        assert forward._request_timeout(b, stream=True).read == 7.0
        assert forward._request_timeout(b).read == 42.0

    def test_build_registry_applies_per_backend_override(self) -> None:
        cfg = MeridianConfig.from_dict({
            "timeouts": {"read": 60.0},
            "resilience": {"circuit_breaker": {"enabled": True, "failure_threshold": 3}},
            "backends": [
                {"name": "fast", "url": "http://a", "timeout": {"read": 5.0}},
                {"name": "slow", "url": "http://b"},
            ],
        })
        reg = build_registry(cfg)
        assert reg.get("fast").timeouts.read == 5.0  # type: ignore[union-attr]
        assert reg.get("slow").timeouts.read == 60.0  # type: ignore[union-attr]
        assert reg.get("fast").circuit.failure_threshold == 3  # type: ignore[union-attr]
        assert reg.get("slow").circuit is not None  # type: ignore[union-attr]


# ── Stream read timeout: graceful end ───────────────────────────────────


class TestStreamReadTimeout:
    async def test_stalled_stream_ends_with_error_and_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def stalled():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            raise httpx.ReadTimeout("stalled")

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stalled(),
            )

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(timeouts=TimeoutConfig(stream_read=1.0))

        resp = await forward.forward_stream(backend, {"model": "m", "stream": True})
        received = bytearray()
        with pytest.raises(forward.StreamUpstreamError):
            async for chunk in resp.body_iterator:
                received.extend(chunk)

        body = bytes(received)
        assert b'"hi"' in body  # partial content preserved
        assert b"meridian_stream_timeout" in body  # terminal error event
        assert body.rstrip().endswith(b"data: [DONE]")  # well-formed end

    async def test_stream_timeout_records_circuit_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def stalled():
            raise httpx.ReadTimeout("stalled")
            yield  # pragma: no cover — keeps it an async generator

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stalled(),
            )

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(
            circuit=CircuitBreaker(CircuitBreakerConfig(enabled=True, failure_threshold=1))
        )

        resp = await forward.forward_stream(backend, {"model": "m", "stream": True})
        with pytest.raises(forward.StreamUpstreamError):
            async for _ in resp.body_iterator:
                pass
        assert backend.circuit.state == "open"

    async def test_midstream_protocol_error_ends_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def flaky():
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.RemoteProtocolError("peer closed")

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=flaky(),
            )

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        resp = await forward.forward_stream(_backend(), {"model": "m", "stream": True})
        received = bytearray()
        with pytest.raises(forward.StreamUpstreamError) as exc_info:
            async for chunk in resp.body_iterator:
                received.extend(chunk)
        assert exc_info.value.error_type == "RemoteProtocolError"
        body = bytes(received)
        assert b'"partial"' in body
        assert body.rstrip().endswith(b"data: [DONE]")


class TestStreamOpenRetry:
    """Retries apply ONLY before the first byte; a started stream never retries."""

    async def test_open_phase_retried_until_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0
        delays: List[float] = []

        async def ok_body():
            yield b"data: [DONE]\n\n"

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("refused")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=ok_body(),
            )

        async def fake_sleep(s: float) -> None:
            delays.append(s)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        before = UPSTREAM_RETRIES.labels(backend="b1")._value.get()
        resp = await forward.forward_stream(
            _backend(),
            {"model": "m", "stream": True},
            ResilienceConfig(max_retries=3, retry_backoff_base=0.1),
        )
        received = bytearray()
        async for chunk in resp.body_iterator:
            received.extend(chunk)
        assert bytes(received).rstrip().endswith(b"data: [DONE]")
        assert attempts == 3
        assert delays == [0.1, 0.2]
        assert UPSTREAM_RETRIES.labels(backend="b1")._value.get() == before + 2

    async def test_open_phase_exhausted_ends_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("refused")

        async def fake_sleep(s: float) -> None:
            pass

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        resp = await forward.forward_stream(
            _backend(),
            {"model": "m", "stream": True},
            ResilienceConfig(max_retries=1, retry_backoff_base=0.01),
        )
        received = bytearray()
        with pytest.raises(forward.StreamUpstreamError) as exc_info:
            async for chunk in resp.body_iterator:
                received.extend(chunk)
        body = bytes(received)
        assert exc_info.value.error_type == "ConnectError"
        assert attempts == 2  # initial + 1 retry, then graceful end
        assert b"meridian_backend_error" in body
        assert body.rstrip().endswith(b"data: [DONE]")

    async def test_open_failure_default_single_attempt_graceful(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("refused")

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        resp = await forward.forward_stream(_backend(), {"model": "m", "stream": True})
        received = bytearray()
        with pytest.raises(forward.StreamUpstreamError) as exc_info:
            async for chunk in resp.body_iterator:
                received.extend(chunk)
        assert attempts == 1  # no resilience config → no retry
        assert exc_info.value.error_type == "ConnectError"
        assert bytes(received).rstrip().endswith(b"data: [DONE]")


# ── Gateway-level: 503 on open circuit ──────────────────────────────────


class TestCircuitOpenApiMapping:
    async def test_open_circuit_maps_to_503(self) -> None:
        # Backend URL on a closed port → connect error on first request,
        # circuit opens (threshold=1), second request is rejected pre-flight.
        cfg = MeridianConfig.from_dict({
            "backends": [{"name": "dead", "url": "http://127.0.0.1:9", "model": "m"}],
            "resilience": {
                "circuit_breaker": {"enabled": True, "failure_threshold": 1},
            },
        })
        await init_app(cfg, start_health=False)
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            first = await c.post("/v1/chat/completions", json=body)
            assert first.status_code == 502  # real connection error
            second = await c.post("/v1/chat/completions", json=body)
            assert second.status_code == 503
            assert second.json()["error"]["type"] == "meridian_circuit_open"

        # Circuit state is visible on the status endpoint
        state = get_state()
        backend = state.registry.get("dead")
        assert backend is not None and backend.circuit is not None
        assert backend.circuit.state == "open"
        assert backend.to_status_dict()["circuit"]["state"] == "open"
