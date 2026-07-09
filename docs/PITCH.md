# How to Pitch Meridian to Clients

A field playbook for selling Meridian into regulated enterprises. The
strategy behind it lives in [`ENTERPRISE_PROPOSAL.md`](./ENTERPRISE_PROPOSAL.md);
this file is what you actually say, show, and send. Keep it synced with
[`ship.md`](./ship.md) — **never pitch a feature that isn't in a tagged
release** (that's a v1.0 gate rule).

_Last updated: 2026-07-09. Shipped basis: Milestones **A–L** on tag **v0.7.0**
(routing, identity, budgets, hardening, **India PII pack**). See
[`ship.md`](./ship.md). Do not pitch **cost attribution** or multi-provider
until those milestones are tagged._

---

## 1. The one-liner

> **Meridian is the compliance layer between your applications and your AI
> models.** It runs on your soil, routes every request to the best backend,
> and produces cryptographically tamper-evident audit logs your regulator
> will accept — without changing a line of application code.

Shorter, for a hallway: *"OpenAI-compatible gateway that makes self-hosted
LLMs auditable and reliable enough for a bank."*

## 2. Who to sell to (ICP)

**Primary:** Indian regulated enterprises moving LLM prototypes to production
— banks/NBFCs (RBI), insurers (IRDAI), healthcare, government/PSU. Signals:
they run or plan vLLM/Ollama on-prem or in a VPC, they have a data-residency
mandate, an internal AI platform team of 2–10.

**Secondary:** any org that got a "no" from security/compliance on public AI
endpoints and owns GPUs.

**Not the customer (yet):** startups happy on public APIs (no compliance
pain, price-sensitive), hyperscale AI-native companies (build in-house), and
anyone whose primary ask is multi-provider cost arbitrage (that's
LiteLLM/Portkey territory — walk away or park them for post-v1.0).

## 3. The problem narrative (open with this)

1. **The mandate:** RBI data-localization, DPDP Act fiduciary duties, IRDAI
   audit-trail requirements mean prompts and derived payloads cannot transit
   public endpoints. Legal exposure, not preference.
2. **The trap:** so they self-host — and discover raw vLLM/Ollama has no
   auth, no tenant isolation, no audit trail, no failover. The platform team
   starts building a gateway in-house.
3. **The reframe:** at their true scale this is not an internet-scale problem
   — a bank's real peak is 50–150 in-flight requests (§1.2 of the enterprise
   proposal). It's an **isolation, security, and auditing** problem. That's
   exactly the layer Meridian is.

## 4. Stakeholder pitches (know who's in the room)

### CISO / compliance head — *sovereignty & proof*
- "Nothing leaves your VPC. Meridian is self-hosted, MIT-licensed, you can
  read every line."
- "Audit logs are metadata-only by default — prompts are never logged — and
  the audit pipeline is tamper-evident: SHA-256 hash chain → Merkle root →
  Ed25519 signature → S3 Object Lock. If anyone alters one byte of history,
  verification fails. You get mathematical proof for the auditor, not a
  promise."
- Demo move: alter a stored audit record live, show verification break.

### VP Engineering / platform lead — *reliability without rework*
- "Drop-in OpenAI-compatible: change `base_url`, done. Streaming included."
- "Token-aware routing stops a 2k-token generation from queueing behind your
  chatbot's 32-token replies; health checks + failover eject a dead vLLM node
  in ~10 seconds with zero app awareness."
- Demo move: `docker stop` a backend mid-load, watch traffic shift on the
  dashboard.

### CFO / finance — *attribution & control*
- "Every request carries an org/team identity. Rate limits per tenant today;
  token budgets and per-team cost reports are the current milestone." *(Check
  ship.md — once J and M ship, upgrade this to present tense.)*
- "License cost is ~20–25% on top of your bare-metal GPU spend, versus the
  regulatory liability of public APIs at the same volume — and versus 6–9
  months of platform-team time building this in-house."

## 5. Demo script (15 minutes, all shipped features)

Prep: `docker compose up --build` on a laptop; auth-enabled config ready.

1. **Drop-in compatibility (2 min)** — point the OpenAI Python SDK at
   Meridian, run a chat completion, show `x-meridian-backend` header.
2. **Routing intelligence (3 min)** — fire the concurrent-request loop from
   the README, show distribution across fast/slow backends on `/ui`.
3. **Failover (3 min)** — `docker stop` the fast backend; dashboard flips to
   unhealthy in ~10 s; requests keep succeeding via the slow backend; restart
   it, watch recovery.
4. **Tenancy (4 min)** — switch to auth config: request without a key → 401;
   with key → 200 and org-tagged logs; request a disallowed model → 403;
   hammer one org's key → 429 while another org sails through.
5. **Audit integrity (3 min)** — show a JSONL/audit event (no prompt in it),
   then the hash-chain verification, then tamper with a record and re-verify
   → failure. Land the line: *"this is what you hand the auditor."*

## 6. Objection handling

| Objection | Response |
|---|---|
| "We'll build this ourselves." | "Your team can — it's 6–9 months of gateway plumbing before they touch your actual product. Meridian is MIT open source: adopt it, audit it, and pay us only for the enterprise controls and support. Build vs buy here is really build vs *fork*." |
| "How is this different from LiteLLM/Portkey?" | "They're API-management proxies aimed at multi-provider SaaS. Meridian is compliance-first and sovereign: on-soil deployment, tamper-evident WORM audit trail, India PII pack *(post-L)*, air-gapped installs. If your problem is regulator-shaped, they aren't in this category." |
| "It's Python — will it be fast enough?" | "The gateway adds low-single-digit ms; your model generates tokens at 80/s. At a bank's true peak of 50–150 in-flight requests, the gateway is never the bottleneck — we publish overhead benchmarks per release." *(Backed by numbers from Milestone K.)* |
| "Single gateway = single point of failure?" | "The gateway is stateless for the data path and restarts in seconds; backends fail over automatically. For hard HA requirements we run active-passive behind your LB today; shared-state HA is on the roadmap." |
| "Is it battle-tested?" | Honest answer pre-v1.0: "We're onboarding design partners now — you'd get direct engineering support and roadmap influence at design-partner pricing." Don't fake maturity; regulated buyers verify. |
| "What about PII in prompts?" | Pre-L: "On the current milestone — India entity pack (Aadhaar/PAN/GSTIN/IFSC/UPI) with block/redact policies. Today prompts already never reach logs." Post-L: demo it. |

## 7. Deployment models & pricing (summary)

Full detail: enterprise proposal §4 and §7.

| Tier | Model | Price point |
|---|---|---|
| Growth | Hybrid VPC, ≤5 backends | $1,200/mo (annual) |
| Scale | Unlimited backends, telemetry routing, PII, 4h SLA | $4,500/mo + overage |
| Sovereign | Air-gapped, offline licenses, signed audit blocks | from $80k/yr |
| **Design partner** *(pre-v1.0 only)* | Scale-tier features, direct eng support | **Free-to-nominal for 2 quarters** in exchange for a written case study/reference and weekly feedback. Cap at 2–3 partners. |

The design-partner tier is the wedge: the v1.0 gate requires one completed
reference PoC ([`V1_ROADMAP.md`](./V1_ROADMAP.md)).

## 8. The 4-week PoC (the close)

Never end a meeting on "we'll think about it" — end on scheduling Week 1.

- **Week 1 — Deploy:** Meridian in their staging VPC, wired to their
  vLLM/Ollama. Success: their app talks through it with only a `base_url`
  change.
- **Week 2 — Shadow:** mirror staging traffic; review dashboards, logs,
  per-org attribution with their platform team.
- **Week 3 — Stress:** enable token-aware routing; kill backends under load;
  measure p95 vs direct. Success: failover invisible to the app.
- **Week 4 — Audit:** run the tamper-demonstration with their security team;
  deliver a one-page ROI memo. Success: security signs off; convert to a
  production license.

Exit criteria are agreed in writing in Week 0 — the PoC is designed to be
un-losable if the product works.

## 9. What NOT to claim (until shipped — check ship.md first)

- ❌ Token budgets / spend caps (Milestone J)
- ❌ PII detection & redaction (Milestone L)
- ❌ Per-team cost reports in ₹/$ (Milestone M)
- ❌ Helm / air-gapped installer (Milestone N)
- ❌ Multi-provider routing, semantic caching (post-v1.0,
  [`FEATURES.md`](./FEATURES.md))

Phrase these as "on the current milestone" with the roadmap doc, never as
present tense. One caught overclaim costs the CISO's trust permanently.

## 10. Follow-up kit (send within 24h of a meeting)

1. This one-liner + link to the GitHub repo (open source = trust artifact)
2. [`ENTERPRISE_PROPOSAL.md`](./ENTERPRISE_PROPOSAL.md) (rendered PDF)
3. `SECURITY.md` — threat model + hardening checklist (CISOs read this first)
4. The 4-week PoC plan with named exit criteria
5. Quickstart: `docker compose up` demo they can run in 10 minutes
