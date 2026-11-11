"""The public site lists the job templates by hand (web/src/website/templates.ts).

The site may not describe a template Meridian does not have, so this fails if the TypeScript copy
drifts from meridian_control/templates.py: same ids, in order, with the same text and sizes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from meridian_control.templates import TEMPLATES

TS = Path(__file__).resolve().parents[2] / "web" / "src" / "website" / "templates.ts"


def _site_templates() -> list[dict]:
    src = TS.read_text(encoding="utf-8")
    body = src[src.index("export const TEMPLATES") : src.index("export const CUSTOM")]
    out = []
    for block in re.split(r"\n  \{\n", body)[1:]:
        entry: dict = {}
        for key in ("id", "title", "summary", "kind", "docs"):
            m = re.search(rf'^    {key}: ("(?:[^"\\]|\\.)*")', block, re.M)
            assert m, f"{key} missing in a site template"
            entry[key] = json.loads(m.group(1))
        for key in ("replicas", "gpus_per_replica", "timeout_seconds"):
            m = re.search(rf"^    {key}: (\d+)", block, re.M)
            assert m, f"{key} missing in a site template"
            entry[key] = int(m.group(1))
        out.append(entry)
    return out


def test_site_templates_match_the_control_plane() -> None:
    site = _site_templates()
    assert [t["id"] for t in site] == [t["id"] for t in TEMPLATES]
    keys = ("title", "summary", "kind", "docs", "replicas", "gpus_per_replica", "timeout_seconds")
    for mine, real in zip(site, TEMPLATES, strict=True):
        for key in keys:
            assert mine[key] == real[key], f"{real['id']}: {key} differs from meridian_control/templates.py"
