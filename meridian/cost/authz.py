"""Authorization for cost usage APIs (enterprise).

Rules when cost.enabled:
- auth.enabled must be true (else 401 — refuse open multi-tenant exports).
- Valid Bearer key required.
- Non-admin keys are forced to their own org (and team if the key has team_id).
- cost_admin keys may query any org/team (or all).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from meridian.api.errors import GatewayError
from meridian.auth import AuthError, IdentityContext, authenticate


def require_usage_identity(
    *,
    auth_enabled: bool,
    key_index: Dict[str, IdentityContext],
    authorization: Optional[str],
) -> IdentityContext:
    """Authenticate for /meridian/usage*. Raises GatewayError on deny."""
    if not auth_enabled:
        raise GatewayError(
            "Usage API requires auth.enabled when cost attribution is on",
            "authentication_error",
            401,
        )
    try:
        return authenticate(authorization, key_index)
    except AuthError as exc:
        raise GatewayError(exc.message, exc.error_type, 401) from exc


def resolve_usage_scope(
    identity: IdentityContext,
    org: Optional[str],
    team: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Return (org_id, team_id) filters for the ledger query.

    Non-admin: always scoped to identity.org_id; team forced if key has team_id.
    Admin: query params pass through (None = all).
    """
    if identity.cost_admin:
        return org, team

    if org is not None and org != identity.org_id:
        raise GatewayError(
            "Not permitted to view usage for another org",
            "permission_error",
            403,
        )
    org_f = identity.org_id

    if identity.team_id:
        if team is not None and team != identity.team_id:
            raise GatewayError(
                "Not permitted to view usage for another team",
                "permission_error",
                403,
            )
        return org_f, identity.team_id

    return org_f, team


def clamp_window_days(requested: int, max_days: int) -> int:
    if requested < 1:
        return 1
    if requested > max_days:
        return max_days
    return requested
