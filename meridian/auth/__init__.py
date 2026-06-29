"""meridian.auth — API-key authentication.

Public interface
----------------
IdentityContext   frozen dataclass representing the caller's identity
AuthError         exception raised when authentication fails
authenticate      validate an Authorization header and return IdentityContext
build_key_index   build a key -> IdentityContext lookup dict from AuthConfig
"""

from __future__ import annotations

from meridian.auth.keys import AuthError, authenticate, build_key_index
from meridian.auth.models import IdentityContext

__all__ = [
    "IdentityContext",
    "AuthError",
    "authenticate",
    "build_key_index",
]
