"""Pure authentication logic for API-key lookup.

No FastAPI imports. This module can be used anywhere in the codebase without
pulling in the web framework.
"""

from __future__ import annotations

import os
import secrets
import string
from typing import Any, Dict, List, Tuple

from meridian.auth.models import IdentityContext
from meridian.config.models import AuthConfig, KeyConfig

_KEY_ALPHABET = string.ascii_letters + string.digits


def derive_key_id(key: str) -> str:
    """Stable, non-secret identifier for a raw key.

    ``mrdn_`` + the first 8 characters of the key body — enough entropy to be
    unique within a deployment's key set, useless for authentication.
    """
    body = key[len("mrdn_"):] if key.startswith("mrdn_") else key
    return f"mrdn_{body[:8]}"


def generate_key() -> str:
    """Generate a random key matching the KeyConfig pattern (mrdn_ + 32 alnum)."""
    return "mrdn_" + "".join(secrets.choice(_KEY_ALPHABET) for _ in range(32))


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


def _identity_from_key(kc: KeyConfig) -> IdentityContext:
    return IdentityContext(
        org_id=kc.org_id,
        team_id=kc.team_id,
        user_id=kc.user_id,
        allowed_models=frozenset(kc.allowed_models),
        pii_policy=kc.pii_policy,
        cost_admin=kc.cost_admin,
        ops_admin=kc.ops_admin,
        role=kc.role,
        key_id=kc.key_id or derive_key_id(kc.key),
    )


def load_keys_from_file(path: str) -> List[KeyConfig]:
    """Load a YAML file with top-level ``keys:`` list (KeyConfig shape)."""
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("keys") if isinstance(data, dict) else None
    if raw is None:
        raise ValueError(f"keys file {path!r} must contain a top-level 'keys' list")
    if not isinstance(raw, list):
        raise ValueError(f"keys file {path!r}: 'keys' must be a list")
    return [KeyConfig.model_validate(item) for item in raw]


def load_keys_with_sources(auth: AuthConfig) -> List[Tuple[KeyConfig, str]]:
    """(KeyConfig, source) pairs; source is 'inline' or the keys_file path."""
    out: List[Tuple[KeyConfig, str]] = [(kc, "inline") for kc in auth.keys]
    if auth.keys_file:
        out.extend((kc, auth.keys_file) for kc in load_keys_from_file(auth.keys_file))
    return out


def save_keys_to_file(path: str, keys: List[KeyConfig]) -> None:
    """Atomically rewrite the keys_file with the given file-source keys.

    Other top-level YAML sections are preserved (comments are not — YAML
    round-tripping is out of scope). Write-temp + rename in the same
    directory so a crash never leaves a half-written file.
    """
    import yaml

    existing: Dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        existing = raw if isinstance(raw, dict) else {}
    existing["keys"] = [
        {k: v for k, v in kc.model_dump(mode="json").items() if v is not None}
        for kc in keys
    ]
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def file_keys_only(auth: AuthConfig) -> List[KeyConfig]:
    """Keys sourced from keys_file (empty when unconfigured)."""
    return load_keys_from_file(auth.keys_file) if auth.keys_file else []


def build_key_index(auth: AuthConfig) -> dict[str, IdentityContext]:
    """Return a mapping of raw key string -> IdentityContext.

    Merges inline ``auth.keys`` with optional ``auth.keys_file``. Duplicates
    across both sources raise ValueError.
    """
    keys: List[KeyConfig] = list(auth.keys)
    if auth.keys_file:
        keys.extend(load_keys_from_file(auth.keys_file))
    seen = [kc.key for kc in keys]
    if len(seen) != len(set(seen)):
        raise ValueError("duplicate API keys across auth.keys and auth.keys_file")
    return {kc.key: _identity_from_key(kc) for kc in keys}


def rebuild_key_index(auth: AuthConfig) -> dict[str, IdentityContext]:
    """Same as build_key_index — named for reload call sites."""
    return build_key_index(auth)


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
