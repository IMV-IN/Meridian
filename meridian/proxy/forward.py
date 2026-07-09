"""HTTP proxy: forward requests to backends with stream/non-stream support.

Client Meridian Authorization is never forwarded upstream. Optional per-backend
``auth_header`` supplies a dedicated upstream credential.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from fastapi.responses import JSONResponse, StreamingResponse

from meridian.registry.backend import Backend

logger = logging.getLogger("meridian.proxy")

_client: Optional[httpx.AsyncClient] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


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


async def forward_non_stream(
    backend: Backend,
    body: Dict[str, Any],
) -> JSONResponse:
    """Forward a non-streaming request and return JSON response."""
    url = f"{backend.url}/v1/chat/completions"
    client = _get_or_create_client()
    resp = await client.post(url, json=body, headers=_upstream_headers(backend))
    return JSONResponse(
        content=resp.json(),
        status_code=resp.status_code,
    )


async def forward_stream(
    backend: Backend,
    body: Dict[str, Any],
) -> StreamingResponse:
    """Forward a streaming request and passthrough SSE bytes."""
    url = f"{backend.url}/v1/chat/completions"
    client = _get_or_create_client()
    headers = _upstream_headers(backend)

    async def stream_generator() -> AsyncIterator[bytes]:
        req = client.build_request("POST", url, json=body, headers=headers)
        resp = await client.send(req, stream=True)
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        except asyncio.CancelledError:
            logger.info("Client disconnected, closing upstream stream to %s", backend.name)
            raise
        finally:
            await resp.aclose()

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )


async def forward_get(backend: Backend, path: str) -> JSONResponse:
    """Forward a GET request to a backend."""
    url = f"{backend.url}{path}"
    client = _get_or_create_client()
    headers: Dict[str, str] = {}
    if backend.auth_header:
        headers["Authorization"] = backend.auth_header
    resp = await client.get(url, headers=headers or None)
    return JSONResponse(content=resp.json(), status_code=resp.status_code)
