"""Metadata-only status reports from a gateway to Meridian Cloud.

In the hybrid model prompts and completions never leave the customer's
network; the hosted console sees only what this module sends:

- backend state (the same view as /meridian/status), canary state
- request counts and latency percentiles for the last interval
- the recent-request ring buffer (ids, model, backend, status, latency,
  org/team/tier) — the same metadata the JSONL log records

Never included: request or response bodies, API keys, upstream credentials,
config file paths.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

import httpx

from meridian.metrics.collectors import REQUESTS_TOTAL
from meridian.util.helpers import now_ms

if TYPE_CHECKING:
    from meridian.api.state import AppState

logger = logging.getLogger("meridian.cloud")

_ID_SAFE = re.compile(r"[^A-Za-z0-9._:@-]+")


def default_gateway_id(port: Optional[int] = None) -> str:
    """Stable per-process identity: ``hostname:port``.

    The port keeps two gateways on one host (or one VM) from overwriting
    each other's reports. Set ``control_plane.gateway_id`` for an identity
    that survives hostname changes (e.g. Kubernetes pod restarts).
    """
    host = _ID_SAFE.sub("-", socket.gethostname())[:100] or "gateway"
    return f"{host}:{port}" if port else host


def _counter_totals() -> Dict[str, float]:
    """Cumulative requests by status class from the Prometheus counter."""
    out = {"2xx": 0.0, "4xx": 0.0, "5xx": 0.0, "other": 0.0}
    for family in REQUESTS_TOTAL.collect():
        for sample in family.samples:
            if not sample.name.endswith("_total"):
                continue
            code = int(sample.labels.get("status", "0") or 0)
            key = "2xx" if 200 <= code < 300 else "4xx" if 400 <= code < 500 else "5xx" if code >= 500 else "other"
            out[key] += sample.value
    return out


def _percentile(sorted_values: list, p: float) -> Optional[float]:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(p * len(sorted_values) + 0.5)) - 1))
    return round(float(sorted_values[idx]), 2)


class CloudReporter:
    def __init__(self, state: "AppState", client: Optional[httpx.AsyncClient] = None) -> None:
        self._state = state
        self._client = client
        self._task: Optional[asyncio.Task[None]] = None
        self._prev_totals: Optional[Dict[str, float]] = None
        self._prev_at: Optional[float] = None
        self._failing = False

    @property
    def _cp(self):
        return self._state.config.control_plane

    def build_report(self) -> Dict[str, Any]:
        from meridian import __version__
        from meridian.api.console import _features

        state = self._state
        cp = self._cp
        now = now_ms()
        totals = _counter_totals()
        window: Dict[str, Any] = {}
        if self._prev_totals is not None and self._prev_at is not None:
            dt = max((now - self._prev_at) / 1000.0, 1e-3)
            d = {k: max(0.0, totals[k] - self._prev_totals.get(k, 0.0)) for k in totals}
            if sum(totals.values()) < sum(self._prev_totals.values()):  # process restarted
                d = dict(totals)
            requests = sum(d.values())
            window = {
                "seconds": round(dt, 1),
                "requests": int(requests),
                "errors": int(d["5xx"]),
                "client_errors": int(d["4xx"]),
                "rps": round(requests / dt, 3),
            }
            cutoff = datetime.fromtimestamp((self._prev_at) / 1000.0, tz=timezone.utc).isoformat()
            lat = sorted(r["latency_ms"] for r in state.recent_requests if r.get("timestamp", "") >= cutoff)
            window["p50_ms"] = _percentile(lat, 0.5)
            window["p95_ms"] = _percentile(lat, 0.95)
        self._prev_totals, self._prev_at = totals, now

        status: Dict[str, Any] = {
            "strategy": state.config.gateway.strategy,
            "backends": [b.to_status_dict() for b in state.registry.all_backends()],
        }
        if state.canary is not None:
            status["canary"] = state.canary.status()
        gateway_id = cp.gateway_id or default_gateway_id(state.config.gateway.port)
        return {
            "gateway_id": gateway_id,
            "name": cp.gateway_name or gateway_id,
            "version": __version__,
            "interval_s": cp.report_interval_s,
            "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": status,
            "window": window,
            "totals": {k: int(v) for k, v in totals.items()},
            "features": _features(state),
            "recent_requests": list(state.recent_requests)[:50],
            # Where workspace members sign in to this gateway's console.
            **({"console_url": state.cloud_sso.console_url} if state.cloud_sso is not None else {}),
        }

    async def send_once(self) -> bool:
        assert self._client is not None
        token = self._cp.resolved_token()
        if not token:
            return False
        url = self._cp.url.rstrip("/") + "/api/gateway/v1/report"
        try:
            resp = await self._client.post(url, json=self.build_report(), headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            if self._state.cloud_sso is not None:
                ws = resp.json().get("workspace_id")
                if isinstance(ws, str) and ws:
                    self._state.cloud_sso.workspace_id = ws
        except (httpx.HTTPError, ValueError) as exc:
            if not self._failing:
                logger.warning("Cloud report failed (%s); will keep retrying quietly", exc)
            self._failing = True
            return False
        if self._failing:
            logger.info("Cloud reports recovered")
        self._failing = False
        return True

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        self._task = asyncio.create_task(self._loop())
        logger.info("Cloud reporter started (every %.0fs to %s)", self._cp.report_interval_s, self._cp.url)

    async def _loop(self) -> None:
        while True:
            await self.send_once()
            await asyncio.sleep(self._cp.report_interval_s)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
