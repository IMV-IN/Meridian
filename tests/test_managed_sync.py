"""Managed-projection sync (DESIGN.md 24 P1): the gateway registers routable
managed engines from meridian-control as dynamic backends, preserves static
config backends, and keeps the last-known-good set when the control plane blips.
"""

from __future__ import annotations

import httpx
import pytest

from meridian.api.state import build_backend, build_registry
from meridian.config.models import BackendConfig, ControlPlaneConfig, MeridianConfig
from meridian.registry.backend import Backend
from meridian.registry.managed import ManagedProjectionSync


def _sync(registry, endpoints_seq):
    """A sync whose httpx client returns each projection payload in turn."""
    calls = iter(endpoints_seq)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(200, json={"endpoints": next(calls)})
        except StopIteration:
            return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = MeridianConfig()
    return ManagedProjectionSync(
        registry, ControlPlaneConfig(enabled=True, url="http://control"),
        build_backend=lambda bc: build_backend(cfg, bc), client=client,
    )


def _ep(engine_id, model, routable=True, endpoint="https://h:8000"):
    return {"engine_id": engine_id, "node_id": "node_1", "model": model,
            "endpoint": endpoint, "provider": "managed", "routable": routable}


@pytest.mark.asyncio
async def test_registers_routable_managed_backend_alongside_static():
    static = Backend(BackendConfig(name="static", url="http://s", model="m0"))
    reg = build_registry(MeridianConfig(backends=[]))
    reg._static = [static]
    reg.set_managed([])  # start with just the static backend
    sync = _sync(reg, [[_ep("e1", "m1"), _ep("e2", "m2", routable=False)]])

    await sync.sync_once()

    names = {b.name for b in reg.all_backends()}
    assert names == {"static", "managed:node_1:e1"}  # non-routable e2 excluded
    assert reg.eligible("m1")[0].name == "managed:node_1:e1"
    assert reg.get("static") is static  # static untouched


@pytest.mark.asyncio
async def test_removed_endpoint_is_deregistered():
    reg = build_registry(MeridianConfig())
    sync = _sync(reg, [[_ep("e1", "m1")], []])  # present, then gone

    await sync.sync_once()
    assert reg.get("managed:node_1:e1") is not None
    await sync.sync_once()
    assert reg.get("managed:node_1:e1") is None


@pytest.mark.asyncio
async def test_fetch_failure_keeps_last_known_set():
    reg = build_registry(MeridianConfig())
    sync = _sync(reg, [[_ep("e1", "m1")]])  # one good payload, then 503s

    await sync.sync_once()
    await sync.sync_once()  # 503 -> keep previous
    assert reg.get("managed:node_1:e1") is not None
