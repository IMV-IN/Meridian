"""Tenant isolation: which backends an org may see (Phase 3).

Pure and deterministic — no I/O, no locking, config in / verdicts out.
Semantics (see ``IsolationConfig``):

- ``shared``: every backend is visible to every org (pre-0.12 behavior).
- ``dedicated``, org listed in ``pools``: only backends whose tags are a
  superset of the org's pool tags. No cross-pool fallback anywhere — that
  guarantee is the entire point of the mode.
- ``dedicated``, org not listed (or no identity): excluded from every
  reserved pool — any backend matching a claimed pool's full tag set.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from meridian.config.models import IsolationConfig
from meridian.registry.backend import Backend


def is_dedicated(cfg: IsolationConfig) -> bool:
    return cfg.mode == "dedicated"


def org_pool_tags(cfg: IsolationConfig, org_id: Optional[str]) -> Optional[Set[str]]:
    """Required pool tags for an org. None = org not pinned (shared or unlisted)."""
    if cfg.mode != "dedicated" or org_id is None:
        return None
    tags = cfg.pools.get(org_id)
    return set(tags) if tags is not None else None


def _reserved_sets(cfg: IsolationConfig) -> List[Set[str]]:
    return [set(tags) for tags in cfg.pools.values() if tags]


def org_can_use(cfg: IsolationConfig, org_id: Optional[str], backend: Backend) -> bool:
    """Visibility check for a single backend under the isolation config."""
    if cfg.mode != "dedicated":
        return True
    pinned = org_pool_tags(cfg, org_id)
    if pinned is not None:
        return pinned.issubset(backend.tags)
    return not any(pool.issubset(backend.tags) for pool in _reserved_sets(cfg))


def filter_visible(
    cfg: IsolationConfig, org_id: Optional[str], backends: Iterable[Backend]
) -> List[Backend]:
    """Apply org visibility to a backend list (no-op in shared mode)."""
    if cfg.mode != "dedicated":
        return list(backends)
    return [b for b in backends if org_can_use(cfg, org_id, b)]
