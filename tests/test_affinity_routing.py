"""Unit tests for affinity-aware routing (meridian.api.routing)."""

from __future__ import annotations

from types import SimpleNamespace

from meridian.api.routing import route
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.affinity import SessionStore
from meridian.router.strategies import RequestContext, create_strategy
from meridian.util.helpers import now_ms


def _state(*, affinity_enabled: bool = True):
    backends = [
        Backend(BackendConfig(name="a", url="http://a", model="m1")),
        Backend(BackendConfig(name="b", url="http://b", model="m1")),
    ]
    registry = BackendRegistry(backends)
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "session_affinity": {"enabled": affinity_enabled, "ttl_s": 600},
    })
    return SimpleNamespace(
        registry=registry,
        strategy=create_strategy("least_inflight"),
        config=cfg,
        session_store=SessionStore(ttl_ms=600_000, max_sessions=100, clock=now_ms),
    )


def _ctx():
    return RequestContext(prompt_tokens=10, max_tokens=10, cost=0.0)


def test_first_request_is_new_then_pinned():
    st = _state()
    b1, _, route1 = route(st, "m1", _ctx(), session_id="sess-1")  # type: ignore[arg-type]
    assert route1 == "new"
    b2, _, route2 = route(st, "m1", _ctx(), session_id="sess-1")  # type: ignore[arg-type]
    assert route2 == "pinned"
    assert b2.name == b1.name


def test_remap_when_pinned_backend_unhealthy():
    st = _state()
    b1, _, _ = route(st, "m1", _ctx(), session_id="sess-1")  # type: ignore[arg-type]
    st.registry.get(b1.name).healthy = False
    b2, _, route2 = route(st, "m1", _ctx(), session_id="sess-1")  # type: ignore[arg-type]
    assert route2 == "remapped"
    assert b2 is not None
    assert b2.name != b1.name


def test_no_session_id_routes_normally():
    st = _state()
    backend, _, session_route = route(st, "m1", _ctx(), session_id=None)  # type: ignore[arg-type]
    assert backend is not None
    assert session_route is None


def test_affinity_disabled_with_session_id_routes_normally():
    st = _state(affinity_enabled=False)
    backend, _, session_route = route(st, "m1", _ctx(), session_id="sess-1")  # type: ignore[arg-type]
    assert backend is not None
    assert session_route is None
