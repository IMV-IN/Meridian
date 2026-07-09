"""Record actual token usage into the cost ledger + metrics."""

from __future__ import annotations

from typing import Optional, Tuple

from meridian.api.state import AppState
from meridian.cost.extract import compute_cost
from meridian.metrics.collectors import TOKENS_TOTAL


def prices_for(state: AppState, model: str) -> Tuple[float, float]:
    cfg = state.config.cost
    mp = cfg.models.get(model)
    if mp is not None:
        return mp.prompt_per_1m, mp.completion_per_1m
    return cfg.default_prompt_per_1m, cfg.default_completion_per_1m


def record_actual_usage(
    state: AppState,
    *,
    model: str,
    org_id: Optional[str],
    team_id: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    if state.cost_ledger is None or not state.config.cost.enabled:
        return
    p_rate, c_rate = prices_for(state, model)
    cost = compute_cost(
        prompt_tokens, completion_tokens,
        prompt_per_1m=p_rate, completion_per_1m=c_rate,
    )
    state.cost_ledger.record(
        org_id=org_id or "",
        team_id=team_id or "",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )
    TOKENS_TOTAL.labels(model=model or "unknown", kind="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(model=model or "unknown", kind="completion").inc(completion_tokens)
