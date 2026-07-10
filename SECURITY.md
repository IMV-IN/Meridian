# Security Policy

Meridian is an L7 inference gateway that sits in the request path between
applications and inference backends. It is designed to run **inside a trusted
network boundary** (VPC, private datacenter LAN). This document describes the
threat model, what Meridian protects, what it explicitly does not, and how to
report vulnerabilities.

_Last reviewed: 2026-07-10 (v0.9.3 / v1.0 gate)._

## Supported versions

| Version | Supported |
|---|---|
| **v0.9.3** (latest tag) and current `main` | ✅ |
| v0.9.x, v0.8.x | Security fixes on a best-effort basis — upgrade to latest 0.9.x |
| older than v0.8 | ❌ — upgrade |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Email the maintainers (see repository owner profile) or use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guided-security-advisories)
on this repository. Include:

- Affected version/tag or commit
- Reproduction steps or proof of concept
- Impact assessment (what an attacker gains)

You can expect an acknowledgement within 72 hours and a fix or mitigation plan
within 14 days for confirmed high-severity issues.

## Threat model

### In scope — what Meridian defends

| Asset | Protection |
|---|---|
| **API access** | Opt-in Bearer-key auth on `/v1/*` (`auth.enabled: true`). Keys map to org/team/user identity. Unknown or missing keys → 401. |
| **Model access** | Per-key `allowed_models` allow-list; out-of-scope models → 403. |
| **Abuse / flooding** | Token-bucket rate limiting keyed per-org (auth on) or per-IP (auth off); org overrides; bounded store (`max_buckets` + idle TTL). |
| **Tenant spend** | Optional org→team→user budgets (tokens + requests, daily/monthly); pre-flight 429; token meters reconciled to backend `usage` after success (0.9.2+). |
| **Resource abuse** | Request body size cap (`gateway.max_body_bytes` → 413). |
| **Container baseline** | Image runs as non-root; Docker `HEALTHCHECK` on `/meridian/status`. |
| **PII in prompts** | Opt-in request-path India pack. Policies: block or redact before forward. **Matched values are never logged** — only entity-type counts. Response body not scanned. |
| **Cost / usage export** | When `cost.enabled`, `/meridian/usage*` **requires auth** and **auth must be enabled at startup**. Non-admin keys see only their org (and team if set). `cost_admin` may export all orgs. |
| **Prompt confidentiality in logs** | Prompts are **never logged by default**. JSONL and audit events are metadata-only. |
| **Upstream key isolation** | Client Meridian `Authorization` is **never** forwarded to backends. Optional `backends[].auth_header` for upstream credentials only. |
| **Audit integrity** | Optional tamper-evident pipeline: SHA-256 hash chain → Merkle tree → Ed25519 → S3 Object Lock (WORM). |
| **Metric cardinality** | Prometheus labels are bounded (backend, model, status, stream, entity type, etc.). Never labeled by prompt text, user id, or raw request id. Org is not on token counters. |

### Out of scope — what Meridian does NOT defend (deploy accordingly)

| Gap | Operator responsibility |
|---|---|
| **TLS termination** | Meridian serves plain HTTP. Terminate TLS at a reverse proxy / LB. |
| **`/metrics`, `/meridian/*`, `/ui`** | Operator plane is **unauthenticated** by design. Restrict at the network layer. |
| **Backend trust** | Backends are assumed trusted; Meridian is not a zero-trust mesh. |
| **Key storage** | Keys live in config or `keys_file`. Protect with secret mounts (mode 0600). External secret managers are operator-side. |
| **SSO / OIDC / full RBAC** | API keys only in 0.9.x. |
| **Response-body PII** | Not scanned. |
| **Multi-process shared meters** | In-process sqlite/memory; multi-replica shared state is future work. |
| **DoS beyond configured limits** | Front with an LB that enforces connection and body limits. |

## Deployment hardening checklist

- [ ] TLS terminated in front of Meridian
- [ ] `auth.enabled: true` with per-org keys; config / `keys_file` permissions 0600
- [ ] `/metrics`, `/meridian/*`, `/ui` blocked from untrusted networks
- [ ] Rate limits and budgets tuned per tenant expectations
- [ ] JSONL log path on a volume with retention/permissions
- [ ] Cost + budget stores on durable volumes with backup (prefer sqlite in prod)
- [ ] Audit pipeline (if enabled): Ed25519 private key outside the container; WORM storage as required
- [ ] Published image scanned (trivy/grype) before production promote — see [`docs/internal/V1_GATE.md`](docs/internal/V1_GATE.md)

## Related

- Design-partner PoC: [`docs/internal/POC_REPORT.md`](docs/internal/POC_REPORT.md)
- Ops runbook: [`docs/OPS_RUNBOOK.md`](docs/OPS_RUNBOOK.md)
- Known residual issues: [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md)
