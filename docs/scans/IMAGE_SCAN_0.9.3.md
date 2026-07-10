# Image scan — Meridian v0.9.3

**Date:** 2026-07-10  
**Scanner:** Trivy **v0.72.0** (`trivy image`)  
**Image:** `meridian:0.9.3-scan` — **built locally from git tag `v0.9.3`**  
(`docker build` on tree at `4664e9d`)

**Published registry note:** `ghcr.io/imv-in/meridian:0.9.3` pull returned
**unauthorized** from this environment (private package auth). Release workflow
for tag `v0.9.3` completed successfully; re-scan the published digest after
`docker login ghcr.io` when available. Local rebuild matches the tagged
Dockerfile + package sources.

**Gate criterion:** “no Critical unfixed” — see §Results. **Not clean** as of
this scan; **v1.0.0 not tagged**.

## Command

```bash
git worktree add /tmp/meridian-0.9.3 v0.9.3
cd /tmp/meridian-0.9.3
docker build -t meridian:0.9.3-scan .
trivy image --severity CRITICAL,HIGH,MEDIUM meridian:0.9.3-scan
```

## Results (CRITICAL + HIGH + MEDIUM)

| Severity | Count |
|----------|------:|
| **CRITICAL** | **4** |
| **HIGH** | **19** |
| **MEDIUM** | **66** |

### CRITICAL (all Debian 12 base; Fixed Version empty in Trivy)

| Package | CVE | Installed | Fixed (Trivy) | Notes |
|---------|-----|-----------|---------------|--------|
| zlib1g | CVE-2023-45853 | 1:1.2.13.dfsg-1 | — | zip overflow; often contested for non-zip paths |
| libsqlite3-0 | CVE-2025-7458 | 3.40.1-2+deb12u2 | — | sqlite integer overflow |
| perl-base | CVE-2026-42496 | 5.36.0-7+deb12u3 | — | Archive::Tar path traversal |
| perl-base | CVE-2026-8376 | 5.36.0-7+deb12u3 | — | regex heap overflow (32-bit noted) |

These are **OS packages in `python:3.11-slim-bookworm`**, not Meridian app code.
No Debian fixed version was listed by Trivy at scan time → gate “Critical
unfixed” is **not met** without base-image refresh / exception policy.

### HIGH highlights (actionable Python tooling)

| Package | CVE | Installed | Fixed |
|---------|-----|-----------|-------|
| wheel | CVE-2026-24049 | 0.45.1 | 0.46.2 |
| jaraco.context | CVE-2026-23949 | 5.3.0 | 6.1.0 |

These are **pip packaging helpers** left in the image after `pip install .`.
Multi-stage build or `pip uninstall` after install would shrink surface.

Remaining HIGH findings are mostly **util-linux / ncurses / gzip / perl** base
packages (same class as CRITICAL).

### Artifacts

| File | Content |
|------|---------|
| `trivy-0.9.3-table.txt` | Full Trivy table output |
| `trivy-0.9.3.json` | Full JSON (local; may be gitignored if large) |

## Recommended follow-ups (product refine — not v1.0 today)

1. **Multi-stage Dockerfile** — build in builder stage; runtime only has app +
   runtime deps (no pip/wheel metadata if avoidable).
2. **Bump base image** when Debian bookworm security updates land for zlib /
   sqlite / perl, or evaluate distroless/python slim alternatives.
3. **Re-scan published GHCR digest** after authenticated pull.
4. **Document exception policy** with cofounder if some CRITICAL OS CVEs are
   accepted (not exploitable on Meridian’s network surface) until base fixes.

## Gate status

| Item | Status |
|------|--------|
| Scan executed and recorded | **Done** |
| Zero CRITICAL unfixed | **Failed** (4 CRITICAL, no FixedVersion) |
| Tag v1.0.0 | **Hold** (per plan + this scan) |
