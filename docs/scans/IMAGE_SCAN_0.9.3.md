# Image scan — Meridian v0.9.3 (hardened Dockerfile)

**Date:** 2026-07-10  
**Scanner:** Trivy **v0.72.0**  
**Image:** `meridian:0.9.3-hardened` built from current `Dockerfile`  
**Base:** `python:3.12-slim-trixie` (Debian 13.5)

## Before → after hardening

| | Original `python:3.11-slim-bookworm` single-stage | Hardened multi-stage (this PR) |
|--|--------------------------------------------------|--------------------------------|
| **CRITICAL** | **4** | **0** |
| **HIGH** | **19** (incl. pip/wheel/jaraco) | **15** (OS only, no FixedVersion) |
| **MEDIUM** | 66 | 48 |
| Python packaging HIGH | wheel, jaraco.context, pip | **0** |

### What we changed

1. Multi-stage build (venv install → runtime copy)
2. Base move to **Debian trixie** (cleared zlib/sqlite CRITICALs)
3. Removed base-image **pip / setuptools / wheel / jaraco**
4. Purged **perl-base** (unused; was CRITICAL Archive::Tar / regex)
5. `apt-get upgrade` on builder + runtime
6. Runtime CMD uses `--loop asyncio`

### Remaining HIGH (Debian essential packages — no FixedVersion)

| Packages (group) | CVE | Notes |
|------------------|-----|--------|
| util-linux / mount / login / libuuid / libblkid / … | CVE-2026-53615 | libblkid DOS partition overflow; **no distro fix listed** |
| ncurses-* / libtinfo | CVE-2025-69720 | buffer overflow; **no distro fix listed** |
| gzip | CVE-2026-41992 | LZH overflow; **no distro fix listed** |
| libacl1 | CVE-2026-54369 | symlink traversal; required by coreutils |

These cannot be purged without breaking `bash`/`coreutils`/`tar`. They are **not Meridian application code**. Exploit paths generally require local privilege or untrusted archives — Meridian’s threat model is private network HTTP gateway.

**Alpine note:** `python:3.12-alpine` scanned **0 CRITICAL / 0 HIGH**, but `aiokafka` failed to build wheels without extra native deps; left as a future packaging option.

## Command

```bash
docker build -t meridian:0.9.3-hardened .
trivy image --severity CRITICAL,HIGH meridian:0.9.3-hardened
```

## Artifacts

| File | Content |
|------|---------|
| `trivy-0.9.3-hardened-table.txt` | CRITICAL+HIGH table |
| `trivy-0.9.3-hardened.json` | Full JSON (gitignored if large) |

## Gate impact

| Criterion | Status |
|-----------|--------|
| CRITICAL unfixed | **Cleared (0)** |
| HIGH fixable (app/tooling) | **Cleared** |
| HIGH no-fix OS | **Documented residual** — accept or wait for Debian |
| Tag v1.0.0 | Still **hold** for cofounder policy + partner sign-off |
