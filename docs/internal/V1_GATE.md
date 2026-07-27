# v1.0 gate — final seal on the complete product

**v1.0 is not a feature dump.** It is a **honesty + evidence** gate — but per the
2026-07-24 sequencing change ([`docs/FULL.md`](../FULL.md)), it now runs **after**
the complete-product track (`v0.10.0` … `v0.13.0`), not as a sign-off blocker on
the bare `v0.9.x` feature set.

Sequencing: code health + resilience + observability/elasticity + platform depth
ship first under honest `0.10`–`0.13` tags; a partner validates the **whole thing**
as an RC; the gate below runs on that RC; `v1.0.0` is the stamp.

_Last updated: 2026-07-24._

## Exit criteria (all required)

| # | Criterion | Evidence | Status |
|---|-----------|----------|--------|
| 1 | Tagged release exists | `v0.9.3` on GitHub + package version | **Done** |
| 2 | Design-partner PoC report | [`POC_REPORT.md`](./POC_REPORT.md) lab run | **Done** (maintainer); re-run on RC required |
| 3 | Real backend proof | Ollama path in [`LOAD.md`](../LOAD.md) + PoC | **Done** |
| 4 | Pitch = code | [`PITCH.md`](./PITCH.md) synced to RC claims | **Update** — must cover resilience, autoscaling, traffic mgmt, isolation when shipped |
| 5 | SECURITY policy current | [`SECURITY.md`](../SECURITY.md) threat model + checklist | **Done**; recheck after Phase 1–3 |
| 6 | Quality gates green on tag | ruff / mypy / pytest + CI on `main` | **Maintained** — every phase tag |
| 7 | Deploy path documented | Helm + air-gap + runbook | **Done**; extend for PDB/Ingress/KEDA when shipped |
| 8 | Image scan | Hardened Dockerfile + Trivy — **0 CRITICAL** — [`scans/IMAGE_SCAN_0.9.3.md`](../scans/IMAGE_SCAN_0.9.3.md) | **Re-run** on RC image |
| 9 | Complete-product track shipped | `v0.10.0` (code health + resilience), `v0.11.0` (observability + elasticity), `v0.12.0` (platform depth) per [`../FULL.md`](../FULL.md) | **Open** |
| 10 | Partner acknowledgement on RC | Sign-off row in PoC report referencing RC tag | **Open** |
| 11 | Tag **v1.0.0** | Only when 1–10 satisfied | **Hold** — runs last, after criteria 4–10 re-verified on the RC |

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
# After RC partner PoC closes and CI green; sign-off + RC image scan attached
git checkout main && git pull
# bump to 1.0.0 in pyproject + meridian/__init__.py + Helm if desired
# or tag v1.0.0 on the commit that freezes docs without code churn
git tag -a v1.0.0 -m "v1.0.0 — design-partner verified complete product"
git push origin v1.0.0
gh release create v1.0.0 --notes-file docs/internal/POC_REPORT.md
```

Until then, **ship and install `v0.9.3`**; the `v0.10`–`v0.13` track is built
on `main` per [`docs/FULL.md`](../FULL.md).
