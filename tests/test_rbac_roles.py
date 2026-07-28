"""RBAC role model (0.9.4): KeyConfig.role, IdentityContext.can_* props,
and the api.authz ops gates."""

from __future__ import annotations

import pytest

from meridian.api.authz import require_ops_view, require_reload
from meridian.api.errors import GatewayError
from meridian.auth import IdentityContext, build_key_index
from meridian.config.models import AuthConfig, KeyConfig

KEY_VIEWER = "mrdn_3kTyXq9Zm4PwR7sN8vBcDfGhJ"
KEY_OPERATOR = "mrdn_9Bv4QwX8Ty2Rs5Np7MfLkHgDc"
KEY_ADMIN = "mrdn_AdM1nKeY5678901234567890Xy"
KEY_PLAIN = "mrdn_Pla1nKeY5678901234567890Zz"
KEY_COSTADMIN = "mrdn_C0stAdm1n567890123456789Qq"


def _index() -> dict[str, IdentityContext]:
    return build_key_index(
        AuthConfig(
            enabled=True,
            keys=[
                KeyConfig(key=KEY_VIEWER, org_id="acme", role="viewer"),
                KeyConfig(key=KEY_OPERATOR, org_id="acme", role="operator"),
                KeyConfig(key=KEY_ADMIN, org_id="acme", role="admin"),
                KeyConfig(key=KEY_PLAIN, org_id="acme"),
                KeyConfig(key=KEY_COSTADMIN, org_id="fin", cost_admin=True),
            ],
        )
    )


# ---------------------------------------------------------------------------
# KeyConfig.role validation
# ---------------------------------------------------------------------------


def test_role_defaults_none():
    kc = KeyConfig(key=KEY_PLAIN, org_id="acme")
    assert kc.role is None


def test_role_rejects_unknown_value():
    with pytest.raises(ValueError):
        KeyConfig(key=KEY_PLAIN, org_id="acme", role="superuser")


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_role_accepts_known_values(role):
    assert KeyConfig(key=KEY_PLAIN, org_id="acme", role=role).role == role


# ---------------------------------------------------------------------------
# IdentityContext derived permissions
# ---------------------------------------------------------------------------


def test_viewer_can_view_but_not_reload_or_read_all_cost():
    idx = _index()
    v = idx[KEY_VIEWER]
    assert v.can_view_ops is True
    assert v.can_reload is False
    assert v.can_read_all_cost is False


def test_operator_can_view_and_reload_but_not_cost():
    o = _index()[KEY_OPERATOR]
    assert o.can_view_ops is True
    assert o.can_reload is True
    assert o.can_read_all_cost is False


def test_admin_can_do_everything():
    a = _index()[KEY_ADMIN]
    assert a.can_view_ops is True
    assert a.can_reload is True
    assert a.can_read_all_cost is True


def test_plain_key_has_no_ops_rights():
    p = _index()[KEY_PLAIN]
    assert p.can_view_ops is False
    assert p.can_reload is False
    assert p.can_read_all_cost is False


def test_legacy_cost_admin_implies_view_and_cost():
    c = _index()[KEY_COSTADMIN]
    assert c.can_read_all_cost is True
    assert c.can_view_ops is True   # finance keys shouldn't need a redundant role
    assert c.can_reload is False


# ---------------------------------------------------------------------------
# api.authz gates
# ---------------------------------------------------------------------------


def test_require_ops_view_open_when_auth_disabled():
    assert require_ops_view(auth_enabled=False, key_index={}, authorization=None) is None


def test_require_ops_view_401_without_key():
    with pytest.raises(GatewayError) as e:
        require_ops_view(auth_enabled=True, key_index=_index(), authorization=None)
    assert e.value.status == 401


def test_require_ops_view_403_for_plain_key():
    with pytest.raises(GatewayError) as e:
        require_ops_view(
            auth_enabled=True, key_index=_index(), authorization=f"Bearer {KEY_PLAIN}"
        )
    assert e.value.status == 403


def test_require_ops_view_allows_viewer():
    ident = require_ops_view(
        auth_enabled=True, key_index=_index(), authorization=f"Bearer {KEY_VIEWER}"
    )
    assert ident is not None and ident.role == "viewer"


def test_require_reload_401_when_auth_disabled():
    with pytest.raises(GatewayError) as e:
        require_reload(auth_enabled=False, key_index={}, authorization=None)
    assert e.value.status == 401


def test_require_reload_403_for_viewer():
    with pytest.raises(GatewayError) as e:
        require_reload(
            auth_enabled=True, key_index=_index(), authorization=f"Bearer {KEY_VIEWER}"
        )
    assert e.value.status == 403


def test_require_reload_allows_operator():
    ident = require_reload(
        auth_enabled=True, key_index=_index(), authorization=f"Bearer {KEY_OPERATOR}"
    )
    assert ident is not None and ident.can_reload
