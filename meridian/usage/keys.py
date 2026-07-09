"""Build MeterKey lists for the org→team→user budget cascade."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from meridian.auth.models import IdentityContext
from meridian.config.models import BudgetConfig, ScopeBudget
from meridian.usage.bucket import period_bucket
from meridian.usage.types import MeterKey


def _append_scope_keys(
    out: List[MeterKey],
    scope: ScopeBudget,
    scope_level: str,
    scope_id: str,
    now: datetime,
) -> None:
    for period_name in ("daily", "monthly"):
        period_caps = getattr(scope, period_name)
        if period_caps is None:
            continue
        bucket = period_bucket(period_name, now)
        if period_caps.tokens is not None:
            out.append(
                MeterKey(
                    scope_level=scope_level,
                    scope_id=scope_id,
                    period=period_name,
                    period_bucket=bucket,
                    metric="tokens",
                    cap=period_caps.tokens,
                )
            )
        if period_caps.requests is not None:
            out.append(
                MeterKey(
                    scope_level=scope_level,
                    scope_id=scope_id,
                    period=period_name,
                    period_bucket=bucket,
                    metric="requests",
                    cap=period_caps.requests,
                )
            )


def build_meter_keys(
    identity: IdentityContext,
    budgets: BudgetConfig,
    now: Optional[datetime] = None,
) -> List[MeterKey]:
    """Return configured meter keys for this identity's cascade levels.

    Levels checked (when configured and identity fields present):
    - org:  ``orgs[org_id]``
    - team: ``teams[org_id/team_id]``
    - user: ``users[org_id/user_id]``
    """
    if now is None:
        now = datetime.now(timezone.utc)

    keys: List[MeterKey] = []

    org_scope = budgets.orgs.get(identity.org_id)
    if org_scope is not None:
        _append_scope_keys(keys, org_scope, "org", identity.org_id, now)

    if identity.team_id:
        team_id = f"{identity.org_id}/{identity.team_id}"
        team_scope = budgets.teams.get(team_id)
        if team_scope is not None:
            _append_scope_keys(keys, team_scope, "team", team_id, now)

    if identity.user_id:
        user_id = f"{identity.org_id}/{identity.user_id}"
        user_scope = budgets.users.get(user_id)
        if user_scope is not None:
            _append_scope_keys(keys, user_scope, "user", user_id, now)

    return keys
