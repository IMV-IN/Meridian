"""Phase 2 packaging guards: Grafana dashboards valid + no drift between the
canonical copies (deploy/grafana/) and the Helm-embedded copies (chart .Files
can only read inside the chart dir, so the two must stay byte-identical).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANON = REPO / "deploy" / "grafana"
CHART = REPO / "deploy" / "helm" / "meridian"

DASHBOARDS = ["dashboard-overview.json", "dashboard-governance.json"]

# Every PromQL expr used in dashboards must reference metrics that exist.
KNOWN_METRICS = {
    "meridian_requests_total",
    "meridian_request_latency_ms_bucket",
    "meridian_backend_inflight",
    "meridian_backend_healthy",
    "meridian_budget_rejections_total",
    "meridian_budget_reconciles_total",
    "meridian_pii_detections_total",
    "meridian_tokens_total",
    "meridian_upstream_retries_total",
    "meridian_backend_circuit_open",
    "meridian_backend_idle",
}


def _walk_exprs(node):
    """Yield every PromQL expr string in a dashboard JSON tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "expr" and isinstance(v, str):
                yield v
            else:
                yield from _walk_exprs(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_exprs(item)


def test_dashboards_parse_and_have_panels() -> None:
    for name in DASHBOARDS:
        dash = json.loads((CANON / name).read_text())
        assert dash["title"].startswith("Meridian"), name
        assert len(dash["panels"]) >= 4, f"{name} looks thin"
        uids = [p["id"] for p in dash["panels"]]
        assert len(set(uids)) == len(uids), f"{name} has duplicate panel ids"


def test_dashboard_exprs_reference_known_metrics() -> None:
    for name in DASHBOARDS:
        dash = json.loads((CANON / name).read_text())
        for expr in _walk_exprs(dash):
            found = set(re.findall(r"meridian_\w+", expr))
            assert found, f"{name}: expr without meridian metric: {expr}"
            unknown = found - KNOWN_METRICS
            assert not unknown, f"{name}: unknown metrics {unknown} in {expr!r}"


def test_no_helm_template_syntax_in_dashboards() -> None:
    # Embedded via .Files.Get — safe, but keep dashboards Helm-clean so a future
    # `tpl` pass can't silently corrupt them.
    for name in DASHBOARDS:
        content = (CANON / name).read_text()
        assert "{{" not in content.replace("{{backend}}", "").replace(
            "{{model}}", ""
        ).replace("{{stream}}", "").replace("{{level}}", "").replace(
            "{{period}}", ""
        ).replace("{{direction}}", "").replace("{{entity}}", "").replace(
            "{{policy}}", ""
        ).replace("{{kind}}", ""), f"{name}: stray Helm-looking syntax"


def test_no_drift_between_canonical_and_chart_copies() -> None:
    for name in DASHBOARDS:
        canon = (CANON / name).read_text()
        chart = (CHART / name).read_text()
        assert canon == chart, (
            f"{name} drifted — edit deploy/grafana/{name} and re-copy into "
            f"deploy/helm/meridian/{name}"
        )
