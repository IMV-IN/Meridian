"""Tests for meridian.auth.keys: build_key_index, AuthError, authenticate."""

import pytest

from meridian.auth import AuthError, IdentityContext, authenticate, build_key_index
from meridian.config.models import AuthConfig, KeyConfig

# ---------------------------------------------------------------------------
# Test keys (valid format: mrdn_ + 20-40 alphanumeric chars)
# ---------------------------------------------------------------------------

KEY_A = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"  # 26 chars after prefix
KEY_B = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"  # 26 chars after prefix
KEY_UNKNOWN = "mrdn_ZZZZZZZZZZZZZZZZZZZZ"  # valid format, not in index


def _auth_config() -> AuthConfig:
    return AuthConfig(
        enabled=True,
        keys=[
            KeyConfig(key=KEY_A, org_id="acme", team_id="eng", user_id="alice"),
            KeyConfig(key=KEY_B, org_id="acme", team_id=None, user_id=None),
        ],
    )


# ---------------------------------------------------------------------------
# build_key_index
# ---------------------------------------------------------------------------


def test_build_key_index_maps_two_keys():
    index = build_key_index(_auth_config())
    assert set(index.keys()) == {KEY_A, KEY_B}

    ctx_a = index[KEY_A]
    assert ctx_a.org_id == "acme"
    assert ctx_a.team_id == "eng"
    assert ctx_a.user_id == "alice"

    ctx_b = index[KEY_B]
    assert ctx_b.org_id == "acme"
    assert ctx_b.team_id is None
    assert ctx_b.user_id is None


def test_build_key_index_empty_config_returns_empty_dict():
    index = build_key_index(AuthConfig())
    assert index == {}


def test_build_key_index_returns_identity_context_instances():
    index = build_key_index(_auth_config())
    for ctx in index.values():
        assert isinstance(ctx, IdentityContext)


# ---------------------------------------------------------------------------
# authenticate - happy paths
# ---------------------------------------------------------------------------


def test_authenticate_valid_bearer_key():
    index = build_key_index(_auth_config())
    ctx = authenticate(f"Bearer {KEY_A}", index)
    assert ctx.org_id == "acme"
    assert ctx.team_id == "eng"
    assert ctx.user_id == "alice"


def test_authenticate_lowercase_bearer_scheme():
    index = build_key_index(_auth_config())
    ctx = authenticate(f"bearer {KEY_B}", index)
    assert ctx.org_id == "acme"
    assert ctx.team_id is None


def test_authenticate_returns_correct_identity_for_second_key():
    index = build_key_index(_auth_config())
    ctx = authenticate(f"Bearer {KEY_B}", index)
    assert ctx.org_id == "acme"
    assert ctx.user_id is None


# ---------------------------------------------------------------------------
# authenticate - missing / malformed header
# ---------------------------------------------------------------------------


def test_authenticate_none_header_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate(None, index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_empty_string_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate("", index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_whitespace_only_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate("   ", index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_non_bearer_scheme_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate("Basic abc123", index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_bearer_with_empty_token_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate("Bearer ", index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_bearer_no_space_raises_invalid_request():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate("Bearer", index)
    assert exc_info.value.error_type == "invalid_request_error"


def test_authenticate_extra_spaces_raises_invalid_request():
    """More than one space between scheme and token is malformed."""
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate(f"Bearer  {KEY_A}", index)
    assert exc_info.value.error_type == "invalid_request_error"


# ---------------------------------------------------------------------------
# authenticate - unknown key
# ---------------------------------------------------------------------------


def test_authenticate_unknown_key_raises_authentication_error():
    index = build_key_index(_auth_config())
    with pytest.raises(AuthError) as exc_info:
        authenticate(f"Bearer {KEY_UNKNOWN}", index)
    assert exc_info.value.error_type == "authentication_error"


# ---------------------------------------------------------------------------
# AuthError shape
# ---------------------------------------------------------------------------


def test_auth_error_stores_message_and_type():
    err = AuthError("something went wrong", "invalid_request_error")
    assert err.message == "something went wrong"
    assert err.error_type == "invalid_request_error"
    assert str(err) == "something went wrong"


def test_auth_error_is_exception():
    err = AuthError("oops", "authentication_error")
    assert isinstance(err, Exception)
