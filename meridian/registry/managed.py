"""Managed-backend sync: poll the meridian-control serving projection and
register routable managed engines as dynamic backends (DESIGN.md 17, 24 P1).

The control plane decides *whether* an engine may receive traffic (valid lease,
Ready observation, not revoked). This poller reflects that decision into the
gateway's routing registry. A fetch failure leaves the last-known-good managed
set in place — a control-plane blip must not blackhole live managed backends.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import httpx

from meridian.config.models import BackendConfig, ControlPlaneConfig
from meridian.registry.backend import Backend, BackendRegistry

logger = logging.getLogger("meridian.managed")


class ManagedProjectionSync:
    def __init__(
        self,
        registry: BackendRegistry,
        config: ControlPlaneConfig,
        build_backend: Callable[[BackendConfig], Backend],
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._build = build_backend
        self._client = client
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        await self.sync_once()  # register before serving traffic
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Managed projection sync started (url=%s interval=%.1fs)",
            self._config.url, self._config.poll_interval_s,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.poll_interval_s)
            await self.sync_once()

    async def sync_once(self) -> None:
        assert self._client is not None
        url = self._config.url.rstrip("/") + "/admin/projection"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            endpoints = resp.json().get("endpoints", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Projection fetch failed (%s); keeping last-known managed set", exc)
            return
        self._registry.set_managed([self._to_backend(e) for e in endpoints if _routable(e)])

    def _to_backend(self, e: dict) -> Backend:
        bc = BackendConfig(
            name=f"managed:{e['node_id']}:{e['engine_id']}",
            url=e["endpoint"],
            model=e.get("model", ""),
            engine="managed",
            tags=[self._config.tag],
        )
        return self._build(bc)


def _routable(e: dict) -> bool:
    return bool(e.get("routable")) and bool(e.get("endpoint"))
