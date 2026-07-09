# Security Policy

Meridian is an L7 inference gateway that sits in the request path between
applications and inference backends. It is designed to run **inside a trusted
network boundary** (VPC, private datacenter LAN). This document describes the
threat model, what Meridian protects, what it explicitly does not, and how to
report vulnerabilities.

## Supported versions

| Version | Supported |
|---|---|
| **v0.7.0** (latest) and current `main` | ✅ |
| older tags | ❌ — upgrade to latest |

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
| **Abuse / flooding** | Token-bucket rate limiting keyed per-org (auth on) or per-IP (auth off); optional org overrides. |
| **Tenant spend** | Optional org→team→user budgets (tokens + requests, daily/monthly); pre-flight 429 when exceeded. |
| **Resource abuse** | Bounded rate-limit key store (idle TTL + max buckets); request body size cap (`gateway.max_body_bytes` → 413). |
| **Container baseline** | Image runs as non-root; Docker `HEALTHCHECK` on `/meridian/status`. |
| **PII in prompts** | Opt-in request-path scan (India pack). Policies: block or redact before forward. **Matched values are never logged** — only entity-type counts in JSONL/audit/metrics. Response body not scanned (v0.7). |
| **Cost / usage export** | When `cost.enabled`, `/meridian/usage*` **requires auth**. Non-admin keys see only their org (and team if set). `cost_admin` keys may export all orgs. Open export refused if auth is off. See `docs/ENTERPRISE_COST.md`. |
| **Prompt confidentiality in logs** | Prompts are **never logged by default**. JSONL logs and audit events are metadata-only (request_id, backend, model, stream, latency, status, org_id/team_id). |
| **Audit integrity** | Optional tamper-evident pipeline: SHA-256 hash chain → Merkle tree → Ed25519 signature → S3 Object Lock (WORM). Any single-byte modification of archived events breaks verification. |
| **Metric cardinality** | Prometheus labels are bounded (backend, model, status, stream — all config-constrained). Never labeled by prompt text, user id, or raw request id. |

### Out of scope — what Meridian does NOT defend (deploy accordingly)

| Gap | Operator responsibility |
|---|---|
| **TLS termination** | Meridian serves plain HTTP. Terminate TLS at a reverse proxy / LB in front of it. |
| **`/metrics`, `/meridian/*`, `/ui`** | These endpoints are **always unauthenticated** by design (operator plane). Restrict them at the network layer — do not expose them to the internet. |
| **Backend trust** | Meridian forwards requests (including the client `Authorization` header) to configured backends. Backends are assumed trusted. |
| **Key storage** | API keys live in plaintext in `config.yaml`. Protect the file (mode 0600, secret mounts). Secret-manager integration is on the roadmap ([docs/V1_ROADMAP.md](docs/V1_ROADMAP.md), Milestone N). |
| **Prompt content inspection** | No PII detection/redaction yet (roadmap Milestone L). Until then, prompt content passes through unmodified. |
| **DoS beyond token buckets** | No request-size caps or connection limits beyond what uvicorn/httpx provide. Front with an LB that enforces body-size limits. |

### Known hardening gaps (tracked)

These are open items, tracked in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md):

1. **Unbounded rate-limit bucket map** — per-IP buckets are never evicted; a
   source-IP spray grows memory. Mitigation until fixed: enable auth (buckets
   key on org, bounded by configured keys) or front with an LB that limits
   unique clients.
2. **Audit event loss on client disconnect mid-stream** — the streaming
   cleanup path can drop the audit event if the client disconnects during SSE.
3. **Container runs as root** — the published image has no `USER` directive.
   Run with `--user` or a pod security context until the image is hardened.

## Deployment hardening checklist

- [ ] TLS terminated in front of Meridian
- [ ] `auth.enabled: true` with per-org keys; `config.yaml` permissions 0600
- [ ] `/metrics`, `/meridian/*`, `/ui` blocked from untrusted networks
- [ ] Rate limits (`rate_limit.token_capacity` / `token_refill_rate`) tuned per tenant expectations
- [ ] JSONL log path on a volume with appropriate retention/permissions
- [ ] Audit pipeline (if enabled): Ed25519 private key stored outside the container; S3 bucket with Object Lock in compliance mode
- [ ] Container run as non-root (`docker run --user 1000:1000 ...` or pod `securityContext`)
- [ ] Body-size limit enforced at the LB (e.g. 1–10 MB depending on max context)
