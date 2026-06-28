"""Workload tiering: map an estimated request shape to a tier + backend tags.

Pure and deterministic. Precedence is fixed: long_prompt, then long_decode,
then default. Used only when ``TieringConfig.enabled`` is True; the API layer
falls back to all healthy backends if the chosen tier's pool is empty.
"""

from __future__ import annotations

from typing import Set, Tuple

from meridian.config.models import TieringConfig
from meridian.router.strategies import RequestContext


def derive_tier(ctx: RequestContext, cfg: TieringConfig) -> Tuple[str, Set[str]]:
    """Return (tier_name, tags) for a request given the tiering config."""
    if ctx.prompt_tokens >= cfg.long_prompt_tokens:
        name = "long_prompt"
    elif ctx.max_tokens >= cfg.long_decode_tokens:
        name = "long_decode"
    else:
        name = "default"
    return name, set(cfg.tiers[name])
