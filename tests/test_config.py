"""Tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError

from meridian.config.models import BackendConfig, MeridianConfig, TieringConfig


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


def test_tiering_config_defaults_disabled():
    cfg = MeridianConfig.from_dict({})
    assert cfg.tiering.enabled is False
    # Default tier->tags map has the three required buckets.
    assert set(cfg.tiering.tiers.keys()) == {"long_prompt", "long_decode", "default"}


def test_tiering_config_parses_thresholds_and_tags():
    cfg = MeridianConfig.from_dict({
        "tiering": {
            "enabled": True,
            "long_prompt_tokens": 4000,
            "long_decode_tokens": 1000,
            "tiers": {
                "long_prompt": ["prefill-pool"],
                "long_decode": ["decode-pool"],
                "default": ["general"],
            },
        }
    })
    assert cfg.tiering.enabled is True
    assert cfg.tiering.long_prompt_tokens == 4000
    assert cfg.tiering.long_decode_tokens == 1000
    assert cfg.tiering.tiers["long_prompt"] == ["prefill-pool"]
    assert cfg.tiering.tiers["long_decode"] == ["decode-pool"]
    assert cfg.tiering.tiers["default"] == ["general"]


def test_tiering_config_rejects_missing_bucket():
    with pytest.raises(ValidationError):
        TieringConfig(tiers={"long_prompt": ["a"], "long_decode": ["b"]})


def test_session_affinity_config_defaults_disabled():
    cfg = MeridianConfig.from_dict({})
    assert cfg.session_affinity.enabled is False
    assert cfg.session_affinity.header == "x-meridian-session"
    assert cfg.session_affinity.ttl_s == 600


def test_session_affinity_config_parses():
    cfg = MeridianConfig.from_dict({
        "session_affinity": {"enabled": True, "ttl_s": 120, "max_sessions": 50}
    })
    assert cfg.session_affinity.enabled is True
    assert cfg.session_affinity.ttl_s == 120
    assert cfg.session_affinity.max_sessions == 50
