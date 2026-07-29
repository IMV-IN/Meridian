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
    cost_admin may query all orgs on /meridian/usage* (enterprise finance).
    ops_admin may call POST /meridian/reload (key rotation).

    role (optional) is the coarse RBAC tier — one of ``viewer``, ``operator``,
    ``admin``. It composes with the legacy ``cost_admin`` / ``ops_admin`` bools
    (either source can grant a right). ``None`` means "no role", so pre-0.9.4
    key files behave exactly as before. Consumers should read the derived
    ``can_*`` properties rather than the raw fields.
    """

    org_id: str
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    allowed_models: FrozenSet[str] = field(default_factory=frozenset)
    pii_policy: Optional[str] = None
    cost_admin: bool = False
    ops_admin: bool = False
    role: Optional[str] = None
    # Stable, non-secret identifier for the presenting key (usage metering,
    # ops views). None for pre-0.12 identities built outside the key index.
    key_id: Optional[str] = None

    @property
    def can_view_ops(self) -> bool:
        """May read operator views: /meridian/status, /meridian/requests, UI data.

        Any role grants read access; an admin/finance bool implies it too, so
        finance and ops keys don't need a redundant role.
        """
        return (
            self.role in ("viewer", "operator", "admin")
            or self.ops_admin
            or self.cost_admin
        )

    @property
    def can_reload(self) -> bool:
        """May call POST /meridian/reload (key rotation)."""
        return self.ops_admin or self.role in ("operator", "admin")

    @property
    def can_manage_keys(self) -> bool:
        """May call the key lifecycle API (POST/DELETE /meridian/keys).

        Narrower than reload: rotating the credential surface is an
        admin-only action, not an operator action.
        """
        return self.ops_admin or self.role == "admin"

    @property
    def can_read_all_cost(self) -> bool:
        """May query any org/team on /meridian/usage* (enterprise finance)."""
        return self.cost_admin or self.role == "admin"
