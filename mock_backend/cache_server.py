"""Deterministic LRU cache simulation for routing tests, never GPU evidence."""

from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from meridian.router.cache_key import prefix_keys


def create_app(capacity: int = 4, delay_ms: float = 5) -> FastAPI:
    app = FastAPI()
    cache: OrderedDict[str, None] = OrderedDict()

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "cache-demo", "object": "model"}]}

    @app.post("/reset_prefix_cache")
    async def reset():
        cache.clear()
        return {"reset": True}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        # Same prefix definition in every experiment arm. The synthetic engine
        # tracks only its first 1024 serialized chars, independent of routing.
        keys = prefix_keys(body, max_checkpoints=4)
        key = keys[0] if keys else ""
        hit = key in cache if key else False
        if key:
            cache[key] = None
            cache.move_to_end(key)
            if len(cache) > capacity:
                cache.popitem(last=False)
        prompt = max(1, sum(len(m.get("content", "")) for m in body.get("messages", [])) // 4)
        usage = {"prompt_tokens": prompt, "completion_tokens": 1,
                 "prompt_tokens_details": {"cached_tokens": int(prompt * .8) if hit else 0}}

        async def events():
            await asyncio.sleep(delay_ms / 1000 * (1 if hit else 3))
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            if body.get("stream_options", {}).get("include_usage"):
                yield f'data: {json.dumps({"choices": [], "usage": usage})}\n\n'
            yield "data: [DONE]\n\n"

        if body.get("stream"):
            return StreamingResponse(events(), media_type="text/event-stream")
        await asyncio.sleep(delay_ms / 1000 * (1 if hit else 3))
        return JSONResponse({"choices": [{"message": {"content": "ok"}}], "usage": usage})

    return app


def from_env() -> FastAPI:
    return create_app(capacity=int(os.environ.get("MOCK_CACHE_ENTRIES", "4")))
