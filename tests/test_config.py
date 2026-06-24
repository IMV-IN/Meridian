"""Tests for configuration loading and validation."""

from meridian.config.models import BackendConfig, MeridianConfig


def test_default_config():
    cfg = MeridianConfig()
    assert cfg.gateway.port == 8080
    assert cfg.gateway.strategy == "least_inflight"
    assert cfg.backends == []


def test_config_from_dict():
    cfg = MeridianConfig.from_dict({
        "gateway": {"port": 9090, "strategy": "ewma_latency"},
        "backends": [
            {"name": "b1", "url": "http://localhost:9001", "model": "gpt-test"},
        ],
    })
    assert cfg.gateway.port == 9090
    assert cfg.gateway.strategy == "ewma_latency"
    assert len(cfg.backends) == 1
    assert cfg.backends[0].name == "b1"


def test_backend_defaults():
    bc = BackendConfig(name="x", url="http://localhost:8000")
    assert bc.weight == 1
    assert bc.health_endpoint == "/v1/models"
    assert bc.tags == []


def test_audit_bus_defaults():
    cfg = MeridianConfig()
    assert cfg.audit_bus.enabled is False
    assert cfg.audit_bus.bootstrap_servers == "localhost:9092"
    assert cfg.audit_bus.topic == "meridian-audit-logs"


def test_audit_bus_from_dict():
    cfg = MeridianConfig.from_dict({
        "audit_bus": {
            "enabled": True,
            "bootstrap_servers": "redpanda:9092",
            "topic": "custom-topic",
            "client_id": "my-gateway",
        },
    })
    assert cfg.audit_bus.enabled is True
    assert cfg.audit_bus.bootstrap_servers == "redpanda:9092"
    assert cfg.audit_bus.topic == "custom-topic"
    assert cfg.audit_bus.client_id == "my-gateway"
