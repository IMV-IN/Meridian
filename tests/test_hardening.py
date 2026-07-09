"""Milestone K integration: body size cap, stream disconnect cleanup."""

from __future__ import annotations

import asyncio
import socket
from typing import AsyncIterator, List
from unittest.mock import MagicMock

import httpx
import pytest

from meridian.api.main import _finalize_request, init_app
from meridian.api.main import app as meridian_app
from meridian.audit.publisher import AuditEvent, AuditEventPublisher
from meridian.config.models import AuditBusConfig, MeridianConfig
from meridian.registry.backend import Backend
from meridian.router.strategies import RequestContext


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _client(**gateway_extra) -> httpx.AsyncClient:
    gw = {"strategy": "least_inflight", **gateway_extra}
    cfg = MeridianConfig.from_dict({
        "gateway": gw,
        "backends": [{
            "name": "dead",
            "url": f"http://127.0.0.1:{_closed_port()}",
            "engine": "mock",
            "model": "demo",
            "weight": 1,
            "health_endpoint": "/v1/models",
        }],
    })
    await init_app(cfg, start_health=False)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=meridian_app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_body_over_content_length_limit_413():
    """Reject when Content-Length exceeds the cap before reading the body."""
    # init_app so _config is set for the body-size check
    await init_app(
        MeridianConfig.from_dict({
            "gateway": {"max_body_bytes": 100},
            "backends": [{
                "name": "dead",
                "url": f"http://127.0.0.1:{_closed_port()}",
                "engine": "mock",
                "model": "demo",
                "weight": 1,
                "health_endpoint": "/v1/models",
            }],
        }),
        start_health=False,
    )

    body = b'{"model":"demo","messages":[]}'
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", b"999999"),
            (b"host", b"test"),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    await meridian_app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


@pytest.mark.asyncio
async def test_body_over_actual_size_limit_413():
    async with await _client(max_body_bytes=64) as c:
        big = b'{"model":"demo","messages":[{"role":"user","content":"' + (b"x" * 200) + b'"}]}'
        resp = await c.post(
            "/v1/chat/completions",
            content=big,
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_body_under_limit_still_routes():
    async with await _client(max_body_bytes=10_000) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "demo", "messages": [{"role": "user", "content": "hi"}]},
        )
    # Dead backend → 502 after passing size gate
    assert resp.status_code == 502


def test_audit_enqueue_is_sync_and_noop_when_disabled():
    pub = AuditEventPublisher(AuditBusConfig(enabled=False))
    # Must not raise; producer is None
    pub.enqueue(AuditEvent(request_id="r1", model="m", backend="b", status_code=200))


def test_finalize_request_decrements_inflight_and_enqueues_audit():
    """Cancel-safe teardown runs fully without awaiting."""
    bc = MagicMock()
    bc.name = "b1"
    bc.healthy = True
    # Real-ish Backend methods via MagicMock side effects
    backend = MagicMock(spec=Backend)
    backend.name = "b1"
    backend.healthy = True

    enqueued: List[AuditEvent] = []

    class CapturingPublisher:
        def enqueue(self, event: AuditEvent) -> None:
            enqueued.append(event)

    import meridian.api.main as main_mod

    old_logger = main_mod._request_logger
    old_pub = main_mod._audit_publisher
    mock_logger = MagicMock()
    main_mod._request_logger = mock_logger
    main_mod._audit_publisher = CapturingPublisher()  # type: ignore[assignment]
    try:
        _finalize_request(
            request_id="req-1",
            model="demo",
            stream=True,
            backend=backend,
            status_code=499,
            start=0.0,
            error_type="client_disconnect",
            request_ctx=RequestContext(prompt_tokens=1, max_tokens=1, cost=1.0),
            tier_name=None,
            session_route=None,
            org_id="acme",
            team_id=None,
        )
    finally:
        main_mod._request_logger = old_logger
        main_mod._audit_publisher = old_pub

    backend.decrement_inflight.assert_called_once()
    backend.subtract_inflight_cost.assert_called_once()
    mock_logger.log.assert_called_once()
    assert len(enqueued) == 1
    assert enqueued[0].status_code == 499
    assert enqueued[0].error_type == "client_disconnect"
    assert enqueued[0].stream is True


@pytest.mark.asyncio
async def test_stream_generator_cleanup_on_cancel():
    """Simulated CancelledError during stream still finalizes via finally path."""
    backend = MagicMock(spec=Backend)
    backend.name = "b1"
    backend.healthy = True

    enqueued: List[AuditEvent] = []

    class CapturingPublisher:
        def enqueue(self, event: AuditEvent) -> None:
            enqueued.append(event)

    import meridian.api.main as main_mod

    old_logger = main_mod._request_logger
    old_pub = main_mod._audit_publisher
    main_mod._request_logger = MagicMock()
    main_mod._audit_publisher = CapturingPublisher()  # type: ignore[assignment]

    async def flaky_upstream() -> AsyncIterator[bytes]:
        yield b"data: hi\n\n"
        raise asyncio.CancelledError()

    status_code = 200
    error_type = None
    request_ctx = RequestContext(prompt_tokens=1, max_tokens=1, cost=2.0)

    async def tracked() -> AsyncIterator[bytes]:
        nonlocal status_code, error_type
        try:
            async for chunk in flaky_upstream():
                yield chunk
        except asyncio.CancelledError:
            error_type = error_type or "client_disconnect"
            status_code = 499
            raise
        finally:
            _finalize_request(
                request_id="req-cancel",
                model="demo",
                stream=True,
                backend=backend,
                status_code=status_code,
                start=0.0,
                error_type=error_type,
                request_ctx=request_ctx,
                tier_name=None,
                session_route=None,
                org_id=None,
                team_id=None,
            )

    try:
        main_mod._request_logger = MagicMock()
        chunks = []
        with pytest.raises(asyncio.CancelledError):
            async for c in tracked():
                chunks.append(c)
    finally:
        main_mod._request_logger = old_logger
        main_mod._audit_publisher = old_pub

    assert chunks == [b"data: hi\n\n"]
    backend.decrement_inflight.assert_called_once()
    assert len(enqueued) == 1
    assert enqueued[0].status_code == 499
