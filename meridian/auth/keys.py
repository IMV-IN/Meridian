"""Pure authentication logic for API-key lookup.

No FastAPI imports. This module can be used anywhere in the codebase without
pulling in the web framework.
"""

from __future__ import annotations

from meridian.auth.models import IdentityContext
from meridian.config.models import AuthConfig


class AuthError(Exception):
    """Raised when a request cannot be authenticated.

    Attributes:
        message:    Human-readable description of the problem.
        error_type: Machine-readable category; one of:
                    ``"invalid_request_error"`` – malformed / missing header.
                    ``"authentication_error"``  – well-formed but unknown key.
    """

    def __init__(self, message: str, error_type: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type


def build_key_index(auth: AuthConfig) -> dict[str, IdentityContext]:
    """Return a mapping of raw key string -> IdentityContext.

    Returns an empty dict when *auth* has no keys configured.
    """
    return {
        kc.key: IdentityContext(
            org_id=kc.org_id,
            team_id=kc.team_id,
            user_id=kc.user_id,
            allowed_models=frozenset(kc.allowed_models),
            pii_policy=kc.pii_policy,
            cost_admin=kc.cost_admin,
        )
        for kc in auth.keys
    }


def authenticate(
    authorization: str | None,
    index: dict[str, IdentityContext],
) -> IdentityContext:
    """Validate the Authorization header and return the caller's identity.

    Args:
        authorization: The raw value of the ``Authorization`` HTTP header,
                       or ``None`` if the header was absent.
        index:         Pre-built key -> IdentityContext mapping from
                       :func:`build_key_index`.

    Returns:
        The :class:`~meridian.auth.models.IdentityContext` associated with
        the presented key.

    Raises:
        AuthError: With ``error_type="invalid_request_error"`` if the header
                   is missing, empty, or not a valid ``Bearer <token>`` form.
        AuthError: With ``error_type="authentication_error"`` if the token is
                   well-formed but does not match any registered key.
    """
    _INVALID = "invalid_request_error"

    # Missing or blank header.
    if not authorization or not authorization.strip():
        raise AuthError("Missing Authorization header", _INVALID)

    # Must be exactly two whitespace-separated parts.
    parts = authorization.split(" ")
    if len(parts) != 2:
        raise AuthError(
            "Invalid Authorization header; expected 'Bearer <key>'",
            _INVALID,
        )

    scheme, token = parts

    # Scheme must be "bearer" (case-insensitive).
    if scheme.lower() != "bearer":
        raise AuthError(
            "Invalid Authorization header; expected 'Bearer <key>'",
            _INVALID,
        )

    # Token must be non-empty after stripping whitespace.
    token = token.strip()
    if not token:
        raise AuthError(
            "Invalid Authorization header; expected 'Bearer <key>'",
            _INVALID,
        )

    # Look up in the index.
    identity = index.get(token)
    if identity is None:
        raise AuthError("Invalid API key", "authentication_error")

    return identity
