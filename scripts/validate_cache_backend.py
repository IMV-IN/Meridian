#!/usr/bin/env python3
"""Reset dedicated engines and require cold/hit cache details in sync and SSE."""

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
        for stream in (False, True):
            reset = await client.post("/reset_prefix_cache")
            reset.raise_for_status()
            if reset.content and reset.json().get("success") is False:
                raise ValueError(f"{url}: prefix cache reset failed")
            for repeated in (False, True):
                payload = {**body, "stream": stream}
                if stream:
                    payload["stream_options"] = {"include_usage": True}
                response = await client.post("/v1/chat/completions", json=payload)
                response.raise_for_status()
                usage = usage_from_sse_bytes(response.content) if stream else usage_from_dict(response.json())
                mode = 'stream' if stream else 'sync'
                if usage is None or not usage.cache_details_available or usage.prompt_tokens <= 0:
                    raise ValueError(f"{url}: missing/invalid cached_tokens ({mode})")
                if stream and not response.content.rstrip().endswith(b"data: [DONE]"):
                    raise ValueError(f"{url}: stream did not terminate with [DONE]")
                if not repeated and usage.cached_tokens != 0:
                    raise ValueError(f"{url}: cache reset did not produce a cold {mode} response")
                if repeated and usage.cached_tokens <= 0:
                    raise ValueError(f"{url}: repeated prefix did not hit cache ({mode})")
                samples.append({"stream": stream, "phase": "repeat" if repeated else "cold",
                                "prompt_tokens": usage.prompt_tokens, "cached_tokens": usage.cached_tokens})
    return {"backend_url": url, "status": "passed", "samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-url", action="append", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reset-caches", action="store_true", help="Allow resets on dedicated test engines")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.reset_caches:
        parser.error("--reset-caches is required; use dedicated experiment engines")

    report = asyncio.run(validate(args.backend_url, args.model))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0 if report["status"] == "passed" else 1


async def validate(urls: list[str], model: str) -> dict:
    checks = []
    for url in urls:
        try:
            checks.append(await probe(url, model))
        except (httpx.HTTPError, ValueError) as exc:
            checks.append({"backend_url": url, "status": "failed", "error": str(exc)})
    return {"model": model, "status": "passed" if all(
        check["status"] == "passed" for check in checks
    ) else "failed", "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
