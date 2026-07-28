"""HTTP proxy: forward requests to backends with stream/non-stream support.

Client Meridian Authorization is never forwarded upstream. Optional per-backend
``auth_header`` supplies a dedicated upstream credential.

Resilience (Phase 1): per-backend timeouts, optional retry with exponential
backoff on transport errors, optional per-backend circuit breaker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from fastapi.responses import JSONResponse, StreamingResponse

from meridian.config.models import ResilienceConfig
from meridian.metrics.collectors import UPSTREAM_RETRIES
from meridian.registry.backend import Backend
from meridian.resilience import CircuitOpenError

logger = logging.getLogger("meridian.proxy")

_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


class StreamReadTimeout(Exception):
    """Upstream SSE read timed out mid-stream (resilience: timeout.stream_read).

    Raised after the client has already been sent a well-formed stream end
    (error event + ``data: [DONE]``); the API layer records it and ends quietly.
    """


def _get_or_create_client() -> httpx.AsyncClient:
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _client is None or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        _client_loop = loop
    return _client


async def close_client() -> None:
    global _client, _client_loop
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
        _client_loop = None


def _upstream_headers(backend: Backend) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    # Never forward the client's Meridian API key. Use backend-specific auth only.
    if backend.auth_header:
        headers["Authorization"] = backend.auth_header
    return headers


def _request_timeout(backend: Backend, *, stream: bool = False) -> httpx.Timeout:
    """Per-request timeout from the backend's effective TimeoutConfig."""
    t = backend.timeouts
    read = t.stream_read if (stream and t.stream_read is not None) else t.read
    return httpx.Timeout(
        connect=t.connect, read=read, write=t.write, pool=t.pool,
    )


def _check_circuit(backend: Backend) -> None:
    if backend.circuit is not None and not backend.circuit.allow_request():
        raise CircuitOpenError(backend.name)


def _circuit_success(backend: Backend) -> None:
    if backend.circuit is not None:
        backend.circuit.record_success()


def _circuit_failure(backend: Backend) -> None:
    if backend.circuit is not None:
        backend.circuit.record_failure()


async def forward_non_stream(
    backend: Backend,
    body: Dict[str, Any],
    resilience: Optional[ResilienceConfig] = None,
) -> JSONResponse:
    """Forward a non-streaming request and return JSON response.

    Retries up to ``resilience.max_retries`` times on transport errors
    (httpx.RequestError — no response was received) with exponential backoff.
    """
    url = f"{backend.url}/v1/chat/completions"
    client = _get_or_create_client()
    headers = _upstream_headers(backend)
    timeout = _request_timeout(backend)

    max_retries = resilience.max_retries if resilience is not None else 0
    backoff_base = resilience.retry_backoff_base if resilience is not None else 0.1

    _check_circuit(backend)
    for attempt in range(1 + max_retries):
        try:
            resp = await client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.RequestError:
            _circuit_failure(backend)
            if attempt >= max_retries:
                raise
            UPSTREAM_RETRIES.labels(backend=backend.name).inc()
            delay = backoff_base * (2 ** attempt)
            logger.info(
                "Retrying %s on %s in %.2fs (attempt %d/%d)",
                "chat/completions", backend.name, delay, attempt + 2, 1 + max_retries,
            )
            await asyncio.sleep(delay)
            continue
        if resp.status_code < 500:
            _circuit_success(backend)
        else:
            _circuit_failure(backend)
        return JSONResponse(
            content=resp.json(),
            status_code=resp.status_code,
        )
    raise AssertionError("unreachable")  # pragma: no cover


async def forward_stream(
    backend: Backend,
    body: Dict[str, Any],
) -> StreamingResponse:
    """Forward a streaming request and passthrough SSE bytes.

    If the upstream read stalls past ``timeout.stream_read`` the client gets a
    well-formed end (SSE error event + ``data: [DONE]``) and the generator
    raises :class:`StreamReadTimeout` so the gateway can account for it.
    """
    url = f"{backend.url}/v1/chat/completions"
    client = _get_or_create_client()
    headers = _upstream_headers(backend)

    _check_circuit(backend)

    async def stream_generator() -> AsyncIterator[bytes]:
        req = client.build_request("POST", url, json=body, headers=headers)
        # send() takes no timeout kwarg — per-request timeout rides on the request.
        req.extensions["timeout"] = _request_timeout(backend, stream=True).as_dict()
        resp: Optional[httpx.Response] = None
        try:
            resp = await client.send(req, stream=True)
            if resp.status_code < 500:
                _circuit_success(backend)
            else:
                _circuit_failure(backend)
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            except httpx.ReadTimeout as exc:
                _circuit_failure(backend)
                logger.warning(
                    "Stream read timeout from %s — closing with [DONE]", backend.name
                )
                yield b'data: {"error":{"message":"upstream read timeout",'
                yield b'"type":"meridian_stream_timeout"}}\n\n'
                yield b"data: [DONE]\n\n"
                raise StreamReadTimeout(backend.name) from exc
        except httpx.RequestError:
            _circuit_failure(backend)
            raise
        finally:
            if resp is not None:
                await resp.aclose()

    async def cancelling_generator() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream_generator():
                yield chunk
        except asyncio.CancelledError:
            logger.info("Client disconnected, closing upstream stream to %s", backend.name)
            raise

    return StreamingResponse(
        cancelling_generator(),
        media_type="text/event-stream",
    )


async def forward_get(backend: Backend, path: str) -> JSONResponse:
    """Forward a GET request to a backend."""
    url = f"{backend.url}{path}"
    client = _get_or_create_client()
    headers: Dict[str, str] = {}
    if backend.auth_header:
        headers["Authorization"] = backend.auth_header
    resp = await client.get(
        url, headers=headers or None, timeout=_request_timeout(backend)
    )
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
