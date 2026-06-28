"""Tests for workload tiering (request shape -> tier name + tags)."""

from meridian.config.models import TieringConfig
from meridian.router.strategies import RequestContext
from meridian.router.tiering import derive_tier


def _cfg() -> TieringConfig:
    return TieringConfig(
        enabled=True,
        long_prompt_tokens=4000,
        long_decode_tokens=1000,
        tiers={
            "long_prompt": ["prefill-pool"],
            "long_decode": ["decode-pool"],
            "default": ["general"],
        },
    )


def _ctx(prompt: int, max_tok: int) -> RequestContext:
    return RequestContext(prompt_tokens=prompt, max_tokens=max_tok, cost=0.0)


def test_default_tier_for_small_request():
    name, tags = derive_tier(_ctx(100, 256), _cfg())
    assert name == "default"
    assert tags == {"general"}


def test_long_prompt_tier_at_threshold():
    name, tags = derive_tier(_ctx(4000, 256), _cfg())
    assert name == "long_prompt"
    assert tags == {"prefill-pool"}


def test_long_decode_tier_at_threshold():
    name, tags = derive_tier(_ctx(100, 1000), _cfg())
    assert name == "long_decode"
    assert tags == {"decode-pool"}


def test_long_prompt_wins_when_both_match():
    # prompt >= 4000 AND max_tokens >= 1000 -> long_prompt has precedence.
    name, tags = derive_tier(_ctx(5000, 2000), _cfg())
    assert name == "long_prompt"
    assert tags == {"prefill-pool"}


def test_just_below_thresholds_is_default():
    name, _ = derive_tier(_ctx(3999, 999), _cfg())
    assert name == "default"
