"""Tests for configuration loading and validation."""

import pytest
from pydantic import ValidationError

from meridian.config.models import AuthConfig, BackendConfig, MeridianConfig, TieringConfig


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


def test_auth_config_defaults_disabled():
    cfg = MeridianConfig.from_dict({})
    assert cfg.auth.enabled is False
    assert cfg.auth.keys == []


def test_auth_config_parses_keys():
    cfg = MeridianConfig.from_dict({
        "auth": {
            "enabled": True,
            "keys": [
                {"key": "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ", "org_id": "acme", "team_id": "eng", "user_id": "alice"},
                {"key": "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc", "org_id": "acme"},
            ],
        }
    })
    assert cfg.auth.enabled is True
    assert len(cfg.auth.keys) == 2
    assert cfg.auth.keys[0].org_id == "acme"
    assert cfg.auth.keys[0].team_id == "eng"
    assert cfg.auth.keys[1].team_id is None


def test_auth_config_rejects_duplicate_keys():
    with pytest.raises(ValidationError):
        AuthConfig(enabled=True, keys=[
            {"key": "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ", "org_id": "a"},
            {"key": "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ", "org_id": "b"},
        ])


def test_auth_config_rejects_bad_key_format():
    with pytest.raises(ValidationError):
        AuthConfig(enabled=True, keys=[{"key": "not-a-valid-key", "org_id": "a"}])


def test_budgets_config_defaults_disabled():
    cfg = MeridianConfig.from_dict({})
    assert cfg.budgets.enabled is False
    assert cfg.budgets.store == "sqlite"
    assert cfg.budgets.sqlite_path == "./meridian_usage.db"


def test_budgets_config_parses_cascade_and_overrides():
    cfg = MeridianConfig.from_dict({
        "budgets": {
            "enabled": True,
            "store": "memory",
            "orgs": {
                "acme": {
                    "daily": {"tokens": 1e6, "requests": 1000},
                    "monthly": {"tokens": 1e7},
                    "token_capacity": 20,
                    "token_refill_rate": 5,
                },
            },
            "teams": {"acme/eng": {"daily": {"tokens": 5e4}}},
            "users": {"acme/alice": {"daily": {"requests": 100}}},
        },
    })
    assert cfg.budgets.enabled is True
    assert cfg.budgets.orgs["acme"].daily.tokens == 1e6
    assert cfg.budgets.orgs["acme"].token_capacity == 20
    assert cfg.budgets.teams["acme/eng"].daily.tokens == 5e4
    assert cfg.budgets.users["acme/alice"].daily.requests == 100
