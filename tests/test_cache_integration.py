"""Actual API/proxy paths, including split SSE chunks and tenant isolation."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from meridian.api.main import app, init_app, shutdown_app
from meridian.config.models import MeridianConfig
from meridian.router.strategies import CacheAffinity


class SplitStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raw = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        raw += b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":1,'
        raw += b'"prompt_tokens_details":{"cached_tokens":90}}}\n\ndata: [DONE]\n\n'
        for offset in range(0, len(raw), 7):
            yield raw[offset:offset + 7]


@pytest.fixture
async def gateway(tmp_path, monkeypatch, request):
    captured = []
    status = [200]

    def upstream(request):
        payload = json.loads(request.content)
        captured.append((request.url.host, payload))
        if payload.get("stream"):
            return httpx.Response(status[0], stream=SplitStream(), headers={"content-type": "text/event-stream"})
        return httpx.Response(status[0], json={"choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 90}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    monkeypatch.setattr("meridian.proxy.forward._get_or_create_client", lambda: client)
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "cache_affinity"},
        "logging": {"jsonl_path": str(tmp_path / "requests.jsonl")},
        "backends": [{"name": n, "url": f"http://{n}", "model": "m"} for n in ("a", "b")],
        **getattr(request, "param", {}),
    })
    state = await init_app(cfg, start_health=False)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as api:
            yield api, state, captured, status, tmp_path
    finally:
        await shutdown_app()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_feedback_changes_routing_without_cost_or_budgets(gateway, stream):
    api, state, captured, _, tmp_path = gateway
    body = {"model": "m", "messages": [{"role": "user", "content": "private text " * 100}], "stream": stream}
    # Start on b; the next otherwise-equal choice would be a without feedback.
    state.registry.get("a").add_inflight_cost(100_000)
    first = await api.post("/v1/chat/completions", json=body)
    assert first.status_code == 200
    state.registry.get("a").subtract_inflight_cost(100_000)
    for backend in state.registry.all_backends():
        backend.ewma_latency_ms = 1
    second = await api.post("/v1/chat/completions", json=body)
    assert second.headers["x-meridian-backend"] == "b"
    assert [name for name, _ in captured] == ["b", "b"]
    assert isinstance(state.strategy, CacheAffinity)
    assert state.strategy.index.size() > 0
    if stream:
        assert captured[0][1]["stream_options"]["include_usage"] is True
        assert second.content.endswith(b"data: [DONE]\n\n")
    assert "private text" not in (tmp_path / "requests.jsonl").read_text()
    metrics = (await api.get("/metrics")).text
    assert 'meridian_cache_affinity_decisions_total{outcome="predicted_hit"}' in metrics


@pytest.mark.asyncio
async def test_failed_upstream_does_not_train_index(gateway):
    api, state, _, status, _ = gateway
    status[0] = 500
    response = await api.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "x" * 1024}],
    })
    assert response.status_code == 500
    assert state.strategy.index.size() == 0


@pytest.mark.asyncio
async def test_stream_http_error_does_not_train_index(gateway):
    api, state, _, status, _ = gateway
    status[0] = 500
    response = await api.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "x" * 1024}], "stream": True,
    })
    assert b'"error"' in response.content
    assert response.content.endswith(b"data: [DONE]\n\n")
    assert state.strategy.index.size() == 0
    assert state.recent_requests[0]["status_code"] == 502


@pytest.mark.asyncio
async def test_incomplete_stream_does_not_train_index(gateway, monkeypatch):
    from fastapi.responses import StreamingResponse

    api, state, _, _, _ = gateway

    async def incomplete():
        yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":1,' \
              b'"prompt_tokens_details":{"cached_tokens":8}}}\n\n'

    async def upstream(*args):
        return StreamingResponse(incomplete(), media_type="text/event-stream")

    monkeypatch.setattr("meridian.api.main.forward_stream", upstream)
    response = await api.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "x" * 1024}], "stream": True,
    })
    assert response.status_code == 200
    assert state.strategy.index.size() == 0


@pytest.mark.asyncio
async def test_unhealthy_prediction_cannot_bypass_candidate_filter(gateway):
    api, state, _, _, _ = gateway
    body = {"model": "m", "messages": [{"role": "user", "content": "x" * 1024}]}
    await api.post("/v1/chat/completions", json=body)
    state.registry.get("a").healthy = False
    response = await api.post("/v1/chat/completions", json=body)
    assert response.headers["x-meridian-backend"] == "b"


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway", [{"auth": {"enabled": True, "keys": [
    {"key": "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ", "org_id": "acme"},
    {"key": "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc", "org_id": "globex"},
]}}], indirect=True)
async def test_other_tenant_does_not_reuse_prediction(gateway):
    api, state, _, _, _ = gateway
    body = {"model": "m", "messages": [{"role": "user", "content": "x" * 1024}]}
    state.registry.get("a").add_inflight_cost(100_000)
    first = await api.post("/v1/chat/completions", json=body,
                           headers={"Authorization": "Bearer mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"})
    assert first.headers["x-meridian-backend"] == "b"
    state.registry.get("a").subtract_inflight_cost(100_000)
    for backend in state.registry.all_backends():
        backend.ewma_latency_ms = 1
    second = await api.post("/v1/chat/completions", json=body,
                            headers={"Authorization": "Bearer mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"})
    assert second.headers["x-meridian-backend"] == "a"


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway", [{"pii": {"enabled": True, "policy": "redact_and_replace"}}], indirect=True)
async def test_prefix_key_uses_body_after_pii_redaction(gateway):
    from meridian.router.cache_key import prefix_keys

    api, state, captured, _, _ = gateway
    body = {"model": "m", "messages": [{"role": "user", "content": "PAN ABCDE1234F " + "x" * 1024}]}
    await api.post("/v1/chat/completions", json=body)
    sent = captured[0][1]
    assert "ABCDE1234F" not in str(sent)
    assert state.strategy.index.lookup(prefix_keys(sent)) is not None
    assert state.strategy.index.lookup(prefix_keys(body)) is None


@pytest.mark.asyncio
async def test_inflight_stream_feedback_uses_own_index_after_reload(gateway, monkeypatch):
    from meridian.api import main as api_main
    from meridian.router.strategies import create_strategy

    api, state, _, _, _ = gateway
    original = state.strategy
    original_forward = api_main.forward_stream

    async def reload_while_opening(*args, **kwargs):
        response = await original_forward(*args, **kwargs)
        state.strategy = create_strategy("cache_affinity")
        return response

    monkeypatch.setattr("meridian.api.main.forward_stream", reload_while_opening)
    response = await api.post("/v1/chat/completions", json={
        "model": "m", "messages": [{"role": "user", "content": "x" * 1024}], "stream": True,
    })
    assert response.status_code == 200
    assert original.index.size() > 0
    assert state.strategy.index.size() == 0


@pytest.mark.asyncio
async def test_cancelled_stream_does_not_train_and_releases_inflight(gateway, monkeypatch):
    from fastapi.responses import StreamingResponse
    from starlette.requests import Request

    from meridian.api.main import chat_completions

    _, state, _, _, _ = gateway
    payload = json.dumps({"model": "m", "messages": [{"role": "user", "content": "x" * 1024}],
                          "stream": True}).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def interrupted():
        yield b'data: {"usage":{"prompt_tokens":10,"completion_tokens":1,' \
              b'"prompt_tokens_details":{"cached_tokens":8}}}\n\n'
        raise asyncio.CancelledError()

    async def upstream(*args):
        return StreamingResponse(interrupted(), media_type="text/event-stream")

    monkeypatch.setattr("meridian.api.main.forward_stream", upstream)
    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions",
                       "headers": [], "client": ("127.0.0.1", 1)}, receive)
    response = await chat_completions(request)
    with pytest.raises(asyncio.CancelledError):
        async for _ in response.body_iterator:
            pass
    assert state.strategy.index.size() == 0
    assert all(backend.inflight == 0 and backend.inflight_cost == 0 for backend in state.registry.all_backends())
    assert state.recent_requests[0]["status_code"] == 499
