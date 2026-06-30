"""Identity model for authenticated requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class IdentityContext:
    """The identity an authenticated request maps to.

    org_id is required (every API key belongs to an org). team_id and user_id
    are optional (org-level keys vs user-level keys). scopes holds the model
    allow-list (Milestone I); empty means all models are permitted.
    """

    org_id: str
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    scopes: FrozenSet[str] = field(default_factory=frozenset)
