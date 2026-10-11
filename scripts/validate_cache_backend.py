#!/usr/bin/env python3
"""Fail closed unless each backend reports cache details in sync and SSE usage."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian.cost.extract import usage_from_dict, usage_from_sse_bytes  # noqa: E402


async def probe(url: str, model: str) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": (
        "Read this reference and answer with one word. " + "The river flows through the valley. " * 80
    )}], "max_tokens": 1, "temperature": 0}
    samples = []
    async with httpx.AsyncClient(base_url=url, timeout=120) as client:
        for stream in (False, False, True):
            payload = {**body, "stream": stream}
            if stream:
                payload["stream_options"] = {"include_usage": True}
            response = await client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            usage = usage_from_sse_bytes(response.content) if stream else usage_from_dict(response.json())
            if usage is None or not usage.cache_details_available or usage.prompt_tokens <= 0:
                raise ValueError(f"{url}: missing/invalid cached_tokens ({'stream' if stream else 'sync'})")
            if stream and not response.content.rstrip().endswith(b"data: [DONE]"):
                raise ValueError(f"{url}: stream did not terminate with [DONE]")
            samples.append({"stream": stream, "prompt_tokens": usage.prompt_tokens,
                            "cached_tokens": usage.cached_tokens})
        if samples[1]["cached_tokens"] <= 0 or samples[2]["cached_tokens"] <= 0:
            raise ValueError(f"{url}: repeated prefixes did not hit cache in sync and stream modes")
    return {"backend_url": url, "status": "passed", "samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", action="append", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    async def run():
        return [await probe(url, args.model) for url in args.backend_url]

    try:
        report = {"model": args.model, "checks": asyncio.run(run())}
    except (httpx.HTTPError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
