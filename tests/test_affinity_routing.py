"""Unit tests for the affinity-aware routing helper in the API layer."""

import meridian.api.main as main
from meridian.config.models import BackendConfig, MeridianConfig
from meridian.registry.backend import Backend, BackendRegistry
from meridian.router.affinity import SessionStore
from meridian.router.strategies import RequestContext, create_strategy
from meridian.util.helpers import now_ms


def _setup(monkeypatch):
    backends = [
        Backend(BackendConfig(name="a", url="http://a", model="m1")),
        Backend(BackendConfig(name="b", url="http://b", model="m1")),
    ]
    registry = BackendRegistry(backends)
    cfg = MeridianConfig.from_dict({
        "gateway": {"strategy": "least_inflight"},
        "session_affinity": {"enabled": True, "ttl_s": 600},
    })
    monkeypatch.setattr(main, "_registry", registry)
    monkeypatch.setattr(main, "_strategy", create_strategy("least_inflight"))
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_session_store",
                        SessionStore(ttl_ms=600_000, max_sessions=100, clock=now_ms))
    return registry


def _ctx():
    return RequestContext(prompt_tokens=10, max_tokens=10, cost=0.0)


def test_first_request_is_new_then_pinned(monkeypatch):
    _setup(monkeypatch)
    b1, _, route1 = main._route("m1", _ctx(), session_id="sess-1")
    assert route1 == "new"
    b2, _, route2 = main._route("m1", _ctx(), session_id="sess-1")
    assert route2 == "pinned"
    assert b2.name == b1.name


def test_remap_when_pinned_backend_unhealthy(monkeypatch):
    registry = _setup(monkeypatch)
    b1, _, _ = main._route("m1", _ctx(), session_id="sess-1")
    registry.get(b1.name).healthy = False  # kill the pinned backend
    b2, _, route2 = main._route("m1", _ctx(), session_id="sess-1")
    assert route2 == "remapped"
    assert b2.name != b1.name


def test_no_session_id_routes_normally(monkeypatch):
    _setup(monkeypatch)
    backend, _, route = main._route("m1", _ctx(), session_id=None)
    assert backend is not None
    assert route is None
