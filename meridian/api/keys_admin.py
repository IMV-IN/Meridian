"""Key lifecycle API logic: list/create/delete API keys (Phase 3, Track 3).

Manageability criteria: keys are administrable without config edits while the
``auth.keys_file`` stays the durable source of truth — every mutation is
written back atomically and the in-memory index is rebuilt immediately.

Scope rules:
- Mutations only touch ``keys_file`` keys; keys defined inline in the main
  config file are refused explicitly (they reappear on reload anyway).
- The full key value is returned exactly once — in the POST response.
  Everywhere else keys are identified by their non-secret prefix id.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from meridian.api.errors import GatewayError
from meridian.api.reload import reload_keys
from meridian.auth.keys import (
    derive_key_id,
    file_keys_only,
    generate_key,
    load_keys_with_sources,
    save_keys_to_file,
)
from meridian.config.models import KeyConfig

if TYPE_CHECKING:
    from meridian.api.state import AppState

logger = logging.getLogger("meridian")

# Serializes create/delete pairs with each other (reloads can only read the
# file, so they can't lose an update — just rebuild slightly early).
_lifecycle_lock = threading.Lock()


class KeyCreateRequest(BaseModel):
    """POST /meridian/keys body. ``key`` optional — generated when omitted."""

    org_id: str = Field(min_length=1)
    key: Optional[str] = Field(
        default=None, pattern=r"^mrdn_[A-Za-z0-9]{20,40}$"
    )
    team_id: Optional[str] = None
    user_id: Optional[str] = None
    key_id: Optional[str] = None
    allowed_models: List[str] = Field(default_factory=list)
    pii_policy: Optional[str] = None
    cost_admin: bool = False
    ops_admin: bool = False
    role: Optional[str] = None


def _require_manageable(state: "AppState") -> None:
    auth = state.config.auth
    if not auth.enabled:
        raise GatewayError(
            "Key lifecycle API requires auth.enabled",
            "authentication_error",
            401,
        )
    if not auth.keys_file:
        raise GatewayError(
            "Key lifecycle API requires auth.keys_file (durable key store)",
            "invalid_request_error",
            400,
        )


def public_key_view(kc: KeyConfig, source: str) -> Dict[str, Any]:
    """Redacted ops view — never includes the raw key."""
    return {
        "key_id": kc.key_id or derive_key_id(kc.key),
        "org_id": kc.org_id,
        "team_id": kc.team_id,
        "user_id": kc.user_id,
        "allowed_models": kc.allowed_models,
        "role": kc.role,
        "cost_admin": kc.cost_admin,
        "ops_admin": kc.ops_admin,
        "source": source,
    }


def list_keys(state: "AppState") -> List[Dict[str, Any]]:
    auth = state.config.auth
    if not auth.enabled:
        raise GatewayError(
            "Key listing requires auth.enabled", "authentication_error", 401
        )
    return [
        public_key_view(kc, source)
        for kc, source in load_keys_with_sources(auth)
    ]


def create_key(
    state: "AppState",
    *,
    org_id: str,
    key: Optional[str] = None,
    team_id: Optional[str] = None,
    user_id: Optional[str] = None,
    key_id: Optional[str] = None,
    allowed_models: Optional[List[str]] = None,
    pii_policy: Optional[str] = None,
    cost_admin: bool = False,
    ops_admin: bool = False,
    role: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Create a key, persist it to keys_file, hot-swap the index.

    Returns (redacted view, full key). The full key is returned only here,
    only once.
    """
    _require_manageable(state)
    auth = state.config.auth
    assert auth.keys_file is not None  # guarded above

    with _lifecycle_lock:
        file_keys = file_keys_only(auth)
        inline_ids = {derive_key_id(kc.key) for kc in auth.keys}
        index_keys = {kc.key for kc in file_keys} | {kc.key for kc in auth.keys}

        candidate = key or generate_key()
        if candidate in index_keys:
            raise GatewayError(
                "Key already exists", "invalid_request_error", 409
            )
        kc = KeyConfig(
            key=candidate,
            org_id=org_id,
            team_id=team_id,
            user_id=user_id,
            key_id=key_id,
            allowed_models=allowed_models or [],
            pii_policy=pii_policy,
            cost_admin=cost_admin,
            ops_admin=ops_admin,
            role=role,
        )
        derived_id = kc.key_id or derive_key_id(kc.key)
        file_ids = {kk.key_id or derive_key_id(kk.key) for kk in file_keys}
        if derived_id in file_ids or derived_id in inline_ids:
            raise GatewayError(
                f"key_id {derived_id!r} already in use",
                "invalid_request_error",
                409,
            )

        file_keys.append(kc)
        save_keys_to_file(auth.keys_file, file_keys)
        n = reload_keys(state)
        logger.info(
            "API key created (id=%s org=%s) — %d key(s) active",
            derived_id, org_id, n,
        )
    return public_key_view(kc, auth.keys_file), candidate


def delete_key(state: "AppState", key_id: str) -> Dict[str, Any]:
    """Delete a keys_file key by its non-secret id. Returns the redacted view.

    Inline keys are refused (they belong to the config file). A deleted key
    keeps working until the index swap completes — which is immediate.
    """
    _require_manageable(state)
    auth = state.config.auth
    assert auth.keys_file is not None  # guarded above

    # Refuse inline keys up-front with a clear message.
    for kc in auth.keys:
        if (kc.key_id or derive_key_id(kc.key)) == key_id:
            raise GatewayError(
                f"Key {key_id!r} is defined inline in the config file; "
                "remove it there and reload",
                "invalid_request_error",
                400,
            )

    with _lifecycle_lock:
        file_keys = file_keys_only(auth)
        matches = [
            kc for kc in file_keys if (kc.key_id or derive_key_id(kc.key)) == key_id
        ]
        if not matches:
            raise GatewayError(
                f"Key {key_id!r} not found", "invalid_request_error", 404
            )
        if len(matches) > 1:
            raise GatewayError(
                f"key_id {key_id!r} is ambiguous — contact the operator",
                "invalid_request_error",
                409,
            )
        victim = matches[0]
        remaining = [kc for kc in file_keys if kc is not victim]
        save_keys_to_file(auth.keys_file, remaining)
        n = reload_keys(state)
        logger.info(
            "API key deleted (id=%s org=%s) — %d key(s) active",
            key_id, victim.org_id, n,
        )
    return public_key_view(victim, auth.keys_file)
