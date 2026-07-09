"""Identity model for authenticated requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class IdentityContext:
    """The identity an authenticated request maps to.

    org_id is required (every API key belongs to an org). team_id and user_id
    are optional (org-level keys vs user-level keys).
    allowed_models is the model allow-list (empty = all models).
    pii_policy optionally overrides the global PII policy for this key.
    """

    org_id: str
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    allowed_models: FrozenSet[str] = field(default_factory=frozenset)
    pii_policy: Optional[str] = None
