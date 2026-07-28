"""Authorization for operator endpoints (0.9.4 RBAC).

Mirrors ``cost/authz.py`` but for the ops surface (/meridian/status,
/meridian/requests, /meridian/reload). All helpers raise ``GatewayError`` on
deny so route handlers can ``except GatewayError`` uniformly.

Gating is default-on when ``auth.enabled``. When auth is disabled these
endpoints stay open, preserving pre-0.9.4 behavior.
"""

from __future__ import annotations

from typing import Dict, Optional

from meridian.api.errors import GatewayError
from meridian.auth import AuthError, IdentityContext, authenticate


def _authenticate(
    key_index: Dict[str, IdentityContext],
    authorization: Optional[str],
) -> IdentityContext:
    try:
        return authenticate(authorization, key_index)
    except AuthError as exc:
        raise GatewayError(exc.message, exc.error_type, 401) from exc


def require_ops_view(
    *,
    auth_enabled: bool,
    key_index: Dict[str, IdentityContext],
    authorization: Optional[str],
) -> Optional[IdentityContext]:
    """Require read access to operator views. Returns None when auth is off."""
    if not auth_enabled:
        return None
    identity = _authenticate(key_index, authorization)
    if not identity.can_view_ops:
        raise GatewayError(
            "Key is not permitted to view operator data",
            "permission_error",
            403,
        )
    return identity


def require_reload(
    *,
    auth_enabled: bool,
    key_index: Dict[str, IdentityContext],
    authorization: Optional[str],
) -> Optional[IdentityContext]:
    """Require key-reload rights. Reload always requires auth to be enabled."""
    if not auth_enabled:
        raise GatewayError(
            "Key reload requires auth.enabled",
            "authentication_error",
            401,
        )
    identity = _authenticate(key_index, authorization)
    if not identity.can_reload:
        raise GatewayError(
            "operator or ops_admin key required for reload",
            "permission_error",
            403,
        )
    return identity
