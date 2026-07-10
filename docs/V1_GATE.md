# v1.0 gate — verification only

**v1.0 is not a feature dump.** It is a **honesty + evidence** gate on the
product already shipped as **0.9.x** (latest tag: **v0.9.3**).

_Last updated: 2026-07-10._

## Exit criteria (all required)

| # | Criterion | Evidence | Status |
|---|-----------|----------|--------|
| 1 | Tagged release exists | `v0.9.3` on GitHub + package version | **Done** |
| 2 | Design-partner PoC report | [`POC_REPORT.md`](./POC_REPORT.md) lab run | **Done** (maintainer); partner sign-off open |
| 3 | Real backend proof | Ollama path in [`LOAD.md`](./LOAD.md) + PoC | **Done** |
| 4 | Pitch = code | [`PITCH.md`](./PITCH.md) synced to v0.9.3 claims | **Done** (this gate PR) |
| 5 | SECURITY policy current | [`SECURITY.md`](../SECURITY.md) threat model + checklist | **Done** (this gate PR) |
| 6 | Quality gates green on tag | ruff / mypy / pytest + CI on `main` | **Done** (0.9.3 ship) |
| 7 | Deploy path documented | Helm + air-gap + runbook | **Done** (N / 0.9.3) |
| 8 | Image scan | Trivy on `meridian:0.9.3-scan` (tag tree rebuild) — see [`scans/IMAGE_SCAN_0.9.3.md`](./scans/IMAGE_SCAN_0.9.3.md) | **Done (failed gate)** — 4 CRITICAL OS CVEs, no FixedVersion; GHCR pull unauth locally |
| 9 | Partner / cofounder acknowledgement | Sign-off row in PoC report | **Open** — after cofounder product chat |
| 10 | Tag **v1.0.0** | Only when 1–9 satisfied **and** CRITICAL policy accepted or fixed | **Hold** — do not tag yet |

## Security deploy checklist (operator)

Copy into the partner’s change ticket:

- [ ] TLS terminated at edge (Meridian is HTTP)
- [ ] `auth.enabled: true`; keys in secret mount (`keys_file`), mode 0600
- [ ] `/metrics`, `/meridian/*`, `/ui` not on the public internet
- [ ] Cost sqlite + budget sqlite on durable volume; backup plan
- [ ] JSONL path retention + disk alerts
- [ ] Rate limits / budgets tuned to tenant SLAs
- [ ] `cost.enabled` implies auth (enforced at startup)
- [ ] Upstream backend credentials use `backends[].auth_header` only — client Meridian keys are **never** forwarded

## What v1.0 may claim

Only items proven on a tagged image, including:

- OpenAI-compatible gateway, stream + non-stream
- Routing strategies, health/failover, session affinity, tiering (as configured)
- Auth keys, budgets (+ reconcile), cost attribution + org-scoped export
- India PII request-path pack (when enabled)
- Helm / air-gap packaging
- Published mock + Ollama overhead guidance

## What v1.0 must not claim

See PoC §4 non-claims and [`PITCH.md`](./PITCH.md) “do not pitch” list.

## Tag procedure (when exit criteria met)

```bash
# After PR merges and CI green; partner sign-off + image scan attached
git checkout main && git pull
# bump to 1.0.0 in pyproject + meridian/__init__.py + Helm if desired
# or tag v1.0.0 on the commit that freezes docs without code churn
git tag -a v1.0.0 -m "v1.0.0 — design-partner verified product"
git push origin v1.0.0
gh release create v1.0.0 --notes-file docs/POC_REPORT.md
```

Until then, **ship and install `v0.9.3`**.
