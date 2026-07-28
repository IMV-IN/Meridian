"""Phase 2: scale-to-zero idle backend signal.

Semantics: a backend with no request traffic for ``backends[].idle_timeout_min``
is marked idle by the health sweep — excluded from routing, health pings paused
(a scaled-to-zero pod SHOULD fail them), ``meridian_backend_idle`` flips to 1.
The first request matching its model/tags wakes it and routes to it.
"""

from __future__ import annotations

import httpx
import pytest

from meridian.api.main import app as meridian_app
from meridian.api.main import get_state, init_app
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.health.checker import HealthChecker
from meridian.metrics.collectors import BACKEND_IDLE
from meridian.registry.backend import Backend, BackendRegistry


def _registry_with_idle() -> tuple[BackendRegistry, Backend, Backend]:
    cfg = MeridianConfig.from_dict({
        "backends": [
            {"name": "hot", "url": "http://hot.test", "model": "m"},
            {
                "name": "cold",
                "url": "http://cold.test",
                "model": "m",
                "idle_timeout_min": 5.0,
            },
        ],
    })
    from meridian.api.state import build_registry

    reg = build_registry(cfg)
    hot = reg.get("hot")
    cold = reg.get("cold")
    assert hot is not None and cold is not None
    return reg, hot, cold


# ── Backend state machine ────────────────────────────────────────────────


class TestIdleMarking:
    def test_no_timeout_never_idles(self) -> None:
        b = Backend(BackendConfig(name="b", url="http://x"))
        assert b.idle_timeout_ms is None
        assert b.mark_idle_if_expired(10**12) is False
        assert b.idle is False

    def test_marks_after_timeout_without_traffic(self) -> None:
        _, _, cold = _registry_with_idle()
        t0 = cold.last_activity_ms
        # Within the window: not idle
        assert cold.mark_idle_if_expired(t0 + 4 * 60_000) is False
        # Past the window: newly idle (idempotent afterwards)
        assert cold.mark_idle_if_expired(t0 + 5 * 60_000) is True
        assert cold.idle is True
        assert cold.mark_idle_if_expired(t0 + 60 * 60_000) is False

    def test_traffic_resets_clock(self) -> None:
        _, _, cold = _registry_with_idle()
        t0 = cold.last_activity_ms
        cold.touch(t0 + 4 * 60_000)  # request at t0+4min
        assert cold.mark_idle_if_expired(t0 + 8 * 60_000) is False  # 4<5 since touch
        assert cold.mark_idle_if_expired(t0 + 9 * 60_000 + 1) is True

    def test_wake_returns_to_active_and_restamps(self) -> None:
        _, _, cold = _registry_with_idle()
        cold.idle = True
        cold.wake(1_000_000.0)
        assert cold.idle is False
        assert cold.last_activity_ms == 1_000_000.0


class TestRouting:
    def test_eligible_skips_idle(self) -> None:
        reg, hot, cold = _registry_with_idle()
        cold.idle = True
        names = [b.name for b in reg.eligible("m")]
        assert names == ["hot"]

    def test_idle_eligible_lists_wake_pool(self) -> None:
        reg, hot, cold = _registry_with_idle()
        cold.idle = True
        assert [b.name for b in reg.idle_eligible("m")] == ["cold"]
        # Unhealthy idle backends don't wake
        cold.healthy = False
        assert reg.idle_eligible("m") == []

    async def test_request_wakes_idle_when_no_active_backend(self) -> None:
        # Only backend is idle-capable; force it idle; request must wake it.
        cfg = MeridianConfig.from_dict({
            "backends": [{
                "name": "cold",
                "url": "http://127.0.0.1:9",
                "model": "m",
                "idle_timeout_min": 5.0,
            }],
        })
        await init_app(cfg, start_health=False)
        state = get_state()
        cold = state.registry.get("cold")
        assert cold is not None
        cold.idle = True

        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            resp = await c.post("/v1/chat/completions", json=body)
        # Routed to the woken backend (connect fails → 502 naming it,
        # NOT 503 no_backend); the gate also proves routing reached it.
        assert resp.status_code == 502
        assert "'cold'" in resp.json()["error"]["message"]
        assert cold.idle is False

    async def test_active_backend_preferred_over_idle(self) -> None:
        cfg = MeridianConfig.from_dict({
            "backends": [
                {"name": "hot", "url": "http://127.0.0.1:9", "model": "m"},
                {
                    "name": "cold",
                    "url": "http://127.0.0.1:9",
                    "model": "m",
                    "idle_timeout_min": 5.0,
                },
            ],
        })
        await init_app(cfg, start_health=False)
        state = get_state()
        cold = state.registry.get("cold")
        assert cold is not None
        cold.idle = True

        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=meridian_app), base_url="http://test"
        ) as c:
            resp = await c.post("/v1/chat/completions", json=body)
        assert "'hot'" in resp.json()["error"]["message"]  # routed hot, not cold
        assert cold.idle is True  # idle did not wake


# ── Health sweep integration ─────────────────────────────────────────────


class TestHealthSweep:
    async def test_sweep_marks_idle_and_skips_ping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reg, hot, cold = _registry_with_idle()
        checker = HealthChecker(reg, MeridianConfig().health, clock=lambda: 10**12)
        checker._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"data": []})
            )
        )

        pings: list[str] = []

        async def spy_get(url: str, *args, **kwargs) -> httpx.Response:
            pings.append(url)
            return httpx.Response(200, json={"data": []})

        monkeypatch.setattr(checker._client, "get", spy_get)
        await checker._check_backend(cold)

        assert cold.idle is True  # marked by the sweep
        assert pings == []  # ... and NOT pinged (scaled-to-zero pods may be down)

        # A woken backend is pinged again
        cold.wake(10**12)
        await checker._check_backend(cold)
        assert pings == ["http://cold.test/v1/models"]


# ── Metrics ──────────────────────────────────────────────────────────────


class TestIdleMetric:
    async def test_gauge_follows_idle_state(self) -> None:
        _, hot, cold = _registry_with_idle()
        # Simulate the /metrics scrape loop writes
        BACKEND_IDLE.labels(backend=hot.name).set(1 if hot.idle else 0)
        BACKEND_IDLE.labels(backend=cold.name).set(1 if cold.idle else 0)
        assert BACKEND_IDLE.labels(backend="hot")._value.get() == 0
        assert BACKEND_IDLE.labels(backend="cold")._value.get() == 0

        cold.idle = True
        BACKEND_IDLE.labels(backend=cold.name).set(1 if cold.idle else 0)
        assert BACKEND_IDLE.labels(backend="cold")._value.get() == 1


class TestStatusSurface:
    def test_status_dict_exposes_idle(self) -> None:
        _, _, cold = _registry_with_idle()
        cold.idle = True
        assert cold.to_status_dict()["idle"] is True
