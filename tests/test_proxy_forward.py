"""Tests for meridian.proxy.forward — stream/non-stream/GET passthrough.

All tests use ``httpx.MockTransport`` so no real sockets are involved. The
module-level httpx client singleton in ``forward.py`` is replaced per-test via
monkeypatch so the mock transport is exercised end-to-end through the public
functions.
"""

from __future__ import annotations

import json
from typing import List, Optional

import httpx
import pytest
from fastapi.responses import JSONResponse, StreamingResponse

import meridian.proxy.forward as forward
from meridian.config.models import BackendConfig
from meridian.registry.backend import Backend


def _backend(name: str = "b1", auth_header: Optional[str] = None) -> Backend:
    return Backend(
        BackendConfig(
            name=name,
            url="http://backend.test",
            model="demo-model",
            auth_header=auth_header,
        )
    )


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> httpx.AsyncClient:
    """Replace the module client singleton with a MockTransport client."""
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(forward, "_client", client)
    monkeypatch.setattr(forward, "_client_loop", None)
    client_returning = lambda: client  # noqa: E731
    monkeypatch.setattr(forward, "_get_or_create_client", client_returning)
    return client


class TestUpstreamHeaders:
    def test_no_auth_header_on_backend_gives_content_type_only(self) -> None:
        headers = forward._upstream_headers(_backend())
        assert headers == {"Content-Type": "application/json"}

    def test_backend_auth_header_is_used(self) -> None:
        headers = forward._upstream_headers(_backend(auth_header="Bearer backend-secret"))
        assert headers["Authorization"] == "Bearer backend-secret"

    def test_client_meridian_key_never_forwarded(self) -> None:
        # The function only receives the Backend; there is no code path that
        # could include a client key, so assert the contract directly.
        headers = forward._upstream_headers(_backend(auth_header="Bearer backend-secret"))
        assert "mrdn_" not in headers.get("Authorization", "")


class TestForwardNonStream:
    async def test_successful_json_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            assert body["model"] == "demo-model"
            assert request.headers["authorization"] == "Bearer backend-secret"
            return httpx.Response(
                200,
                json={"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": "hi"}}]},
            )

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))
        backend = _backend(auth_header="Bearer backend-secret")

        resp = await forward.forward_non_stream(backend, {"model": "demo-model"})

        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 200
        payload = json.loads(resp.body.decode())
        assert payload["id"] == "chatcmpl-1"

    async def test_error_status_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"message": "down"}})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        resp = await forward.forward_non_stream(_backend(), {"model": "m"})

        assert resp.status_code == 503
        assert b'"down"' in resp.body

    async def test_request_sent_to_backend_completions_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_urls: List[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        await forward.forward_non_stream(_backend(), {"model": "m"})

        assert seen_urls == ["http://backend.test/v1/chat/completions"]


class TestForwardStream:
    async def test_sse_bytes_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            content = b"".join(chunks)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=content,
            )

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        resp = await forward.forward_stream(_backend(), {"model": "m", "stream": True})

        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"

        received = bytearray()
        async for chunk in resp.body_iterator:
            received.extend(chunk)
        body = bytes(received)
        assert body.endswith(b"data: [DONE]\n\n")
        assert b'"Hello"' in body and b'" world"' in body

    async def test_stream_uses_backend_auth_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer b-secret"
            return httpx.Response(200, content=b"data: [DONE]\n\n")

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        resp = await forward.forward_stream(
            _backend(auth_header="Bearer b-secret"), {"model": "m", "stream": True}
        )
        async for _ in resp.body_iterator:
            pass


class TestForwardGet:
    async def test_get_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url) == "http://backend.test/v1/models"
            # No backend auth configured → no Authorization header
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"data": [{"id": "demo-model"}]})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        resp = await forward.forward_get(_backend(), "/v1/models")

        assert resp.status_code == 200
        payload = json.loads(resp.body.decode())
        assert payload["data"][0]["id"] == "demo-model"

    async def test_get_includes_backend_auth_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer b-secret"
            return httpx.Response(200, json={"ok": True})

        _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        await forward.forward_get(_backend(auth_header="Bearer b-secret"), "/v1/models")


class TestClientLifecycle:
    async def test_close_client_resets_singleton(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        client = _install_mock_client(monkeypatch, httpx.MockTransport(handler))

        await forward.close_client()

        assert forward._client is None
        assert forward._client_loop is None
        assert client.is_closed

    async def test_close_client_is_safe_when_none(self) -> None:
        forward._client = None
        forward._client_loop = None
        await forward.close_client()  # must not raise
        assert forward._client is None
