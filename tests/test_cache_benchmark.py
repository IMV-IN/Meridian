"""Reproducible workload, cache signal prerequisite, and executable sweep smoke."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from mock_backend.cache_server import create_app
from scripts.bench_cache_routing import workload
from scripts.validate_cache_backend import probe, validate

ROOT = Path(__file__).resolve().parents[1]


def test_workload_sharing_and_determinism():
    kwargs = {"seed": 7, "working_set": 2, "prompt_chars": 2048}
    unique = workload(20, 0, **kwargs)
    shared = workload(20, 100, **kwargs)
    assert unique == workload(20, 0, **kwargs)
    def leading(items):
        return {body["messages"][0]["content"][:1024] for body in items}

    assert len(leading(unique)) == 20
    assert len(leading(shared)) == 2
    assert not leading(unique) & leading(workload(2, 0, offset=-2, **kwargs))


@pytest.mark.asyncio
async def test_probe_requires_sync_and_stream_cache_hits(monkeypatch):
    client_class = httpx.AsyncClient
    app = create_app(delay_ms=0)
    monkeypatch.setattr("scripts.validate_cache_backend.httpx.AsyncClient", lambda **kwargs:
                        client_class(**kwargs, transport=httpx.ASGITransport(app=app)))
    report = await probe("http://engine", "cache-demo")
    assert report["status"] == "passed"
    assert report["samples"][1]["cached_tokens"] > 0
    assert report["samples"][2]["cached_tokens"] > 0
    assert "river" not in json.dumps(report)


@pytest.mark.asyncio
async def test_probe_rejects_missing_cache_signal(monkeypatch):
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        "usage": {"prompt_tokens": 10, "completion_tokens": 1},
    }))
    monkeypatch.setattr("scripts.validate_cache_backend.httpx.AsyncClient", lambda **kwargs:
                        client_class(**kwargs, transport=transport))
    with pytest.raises(ValueError, match="missing/invalid cached_tokens"):
        await probe("http://engine", "m")


@pytest.mark.asyncio
async def test_validation_preserves_failures_and_checks_remaining_backends(monkeypatch):
    checked = []

    async def fake_probe(url, model):
        checked.append((url, model))
        if url == "http://unsupported":
            raise ValueError(f"{url}: missing/invalid cached_tokens (sync)")
        return {"backend_url": url, "status": "passed", "samples": []}

    monkeypatch.setattr("scripts.validate_cache_backend.probe", fake_probe)
    report = await validate(["http://unsupported", "http://compatible"], "m")
    assert report["status"] == "failed"
    assert [check["status"] for check in report["checks"]] == ["failed", "passed"]
    assert report["checks"][0]["error"].endswith("missing/invalid cached_tokens (sync)")
    assert checked == [("http://unsupported", "m"), ("http://compatible", "m")]


def test_three_arm_benchmark_exports_metadata_only(tmp_path):
    output, rows = tmp_path / "report.json", tmp_path / "rows.csv"
    result = subprocess.run([
        sys.executable, "scripts/bench_cache_routing.py", "--requests", "3", "--warmup", "0",
        "--sharing-ratios", "0", "100", "--repetitions", "1", "--output", str(output), "--csv", str(rows),
    ], cwd=ROOT, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["mode"] == "synthetic_mock"
    assert len(report["summaries"]) == 6
    assert {row["arm"] for row in report["summaries"]} == {"A", "B", "C"}
    assert all(row["requests"] == 3 and row["errors"] == 0 for row in report["summaries"])
    with rows.open() as handle:
        samples = list(csv.DictReader(handle))
    assert len(samples) == 18
    assert all(float(row["ttft_ms"]) > 0 and row["backend"] and row["request_id"] for row in samples)
    assert "word" not in rows.read_text()
