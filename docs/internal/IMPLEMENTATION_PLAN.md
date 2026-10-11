# Remaining implementation plan

Updated 2026-10-12 after local and GitHub inspection. This plan supersedes stale
"next milestone" statuses in the July roadmap documents. It does not authorize
automatic releases or expand the v1.0 gate to require every future feature.

## Starting point

- Gateway v0.12.0 is tagged. Remote main at `45fec9d` includes the separate control
  service and gateway projection integration; these are newer than that release.
- Main's gateway CI and control CI passed. The control run skipped cross-repo
  cases because the optional meridian-node checkout failed.
- Cache-aware routing is implemented locally on `capstone/kv-cache-aware-routing`:
  prefix index, correction, usage extraction, sync/SSE feedback, metrics,
  executable three-arm harness, and GPU prerequisite probe.
- [Local GPU validation](../validation/KV_CACHE_VLLM.md) is complete: both native
  vLLM 0.24.0 engines passed cold/hit JSON/SSE checks; cache eviction was verified;
  6,336 measured requests completed without failures across two full sweeps and
  a higher-weight check. Correction shows mixed performance, so KV stays
  experimental and opt-in. Docker Desktop is stopped; the system daemon lacks
  an NVIDIA runtime. The native experiment engines have been stopped.
- v1.0 still requires a partner-run RC PoC, current security/image evidence,
  synchronized claims, and written sign-off.

## Sequence and acceptance gates

| Order | Work package | Dependency | Exit condition |
|---|---|---|---|
| 1 | KV engine evidence — complete locally | Two dedicated native vLLM 0.24.0 instances | Probe, eviction calibration, two full sweeps, weight check and raw rows published; benefit remains unproven |
| 2 | Reconcile release and documentation metadata | Decide which tested commits belong in the next RC | Package, chart, image, release links and status docs agree; clean install/smoke passes |
| 3 | Close control deployment validation gaps | Complete control dependencies; access to meridian-node | Cross-repo CI actually runs; migration, edge identity, revocation and restore drills pass |
| 4 | Validate a partner RC and close v1.0 | Packages 2–3; package 1 if KV is included in the RC | Partner PoC, scan, claims and sign-offs attached to the exact RC |
| 5 | Implement partner-selected scale/capability work | Measured pilot requirement | Separate milestone/PR with tests, docs and validation evidence |

Packages 1–3 can progress independently where dependencies allow. Keep KV
experimental if live feedback is unavailable or results do not justify enabling
it. It must not silently become a v1.0 blocker for the already-shipped gateway.

## 1. KV evidence — locally complete; keep experimental

The [executed report](../validation/KV_CACHE_VLLM.md) satisfies the local evidence
checklist below, including null/adverse results and a locality-weight sensitivity
check. Do not enable KV by default or claim a performance win. A separate-GPU,
representative workload study is needed before making those decisions. Next
implementation work is package 2 (release consistency), then package 3 (control
deployment validation); partner sign-off remains the v1.0 gate.

Use [`../KV_CACHE_ROUTING.md`](../KV_CACHE_ROUTING.md) for the executable recipe.

1. Start the two pinned vLLM instances. Run `validate_cache_backend.py`; require
   nonzero repeated-prefix hits in both sync and SSE responses on each engine.
2. Calibrate cache pressure **before** the comparison: vary working-set size,
   prompt length and available KV memory until eviction is observable. Preserve
   engine versions, model revision, GPU layout and all launch flags.
3. Run arms A/B/C across 0/25/50/75/100% prefix sharing, with identical workloads,
   fixed offered load below saturation, excluded warmup, cache resets and at
   least three repetitions. Include a separate closed-loop contention run.
4. Publish per-repeat TTFT p50/p95, cache-hit ratio, prediction accuracy and raw
   metadata rows. Report spread and whether C improves over B. A result where
   the arms tie is valid evidence, not permission to claim a benefit.
5. Tune locality weight against measured load scores. Verify an overloaded
   predicted backend loses to a healthy idle alternative; retain cold/missing
   feedback behavior and tenant isolation checks.
6. Run ruff, mypy and all tests on the exact candidate. Review the patch, create
   its milestone PR and attach evidence. Choose an unreleased/experimental
   release note if the performance hypothesis is still unproven.

Known limits: text-prefix approximation, per-process index, aggregate cache-hit
feedback, and two engines sharing one GPU. Tokenizer-aware matching, distributed
prefix state and engine-native residency adapters require a separate measured
design; they are not hidden unfinished parts of this implementation.

## 2. Release and documentation consistency

Concrete fixes for a dedicated maintenance PR:

- Align Helm `version`/`appVersion` with the chosen release; they currently say
  0.11.0 while the package says 0.12.0. Add a meaningful release consistency check
  covering the chart as well as the package and image tag.
- Correct GitHub's latest-release designation: v0.12.0 exists, while the latest
  release endpoint currently returns v0.11.0. Verify the README badge and link
  resolve to the intended release after the change.
- Reconcile V1_GATE, V1_ROADMAP, FULL, ROADMAP and ship statuses against code and
  actual tags. Replace old v0.9.3 installation instructions where inappropriate;
  retain historical evidence with its original version.
- Record the control service as newer than the gateway release. Decide its
  versioning/image publication contract explicitly rather than implying the
  v0.12.0 gateway image contains it.
- Run Docker quickstart and Helm render/install smoke from the release candidate;
  verify streams, failover, headers, metrics and metadata-only logging.

Exit evidence: clean-clone instructions, chart output and smoke logs linked from
the PR; no version/tag/image mismatch. Publishing a release is the final action
after the candidate is reviewable and its release gate is satisfied.

## 3. Control service: complete validation before production

The service is implemented. The remaining work is validating its deployment
boundaries and closing gaps proven by those checks.

- **Full environment:** install `.[control,dev]` and the matching meridian-node
  checkout. Run control mypy, migrations, cross-repo tests and
  `scripts/verify_connection.py`. The local gateway venv lacks Alembic and the
  node package; its control type error must be rechecked with those installed.
- **Required cross-repo CI:** configure a narrowly scoped read token or a
  suitable checkout mechanism, pin the compatible node revision, and fail the
  release-validation job if that checkout is missing. Optional skips can remain
  appropriate for ordinary fork CI, but cannot count as RC compatibility proof.
- **Operator boundary:** document and test authentication/authorization for
  `/admin/*` at the deployment edge, or implement service-side admin credentials.
  The current routes have no application-level admin authentication. Give the
  gateway read-only projection access; it must not inherit mutation privileges.
  Test unauthenticated mutation rejection and intended operator access.
- **mTLS/CRL:** validate real edge TLS termination, verified certificate-header
  forwarding and blocked direct app access. Prove rotated and revoked node
  certificates behave correctly, including CRL propagation to the edge. The
  service exposes CRL data; distribution automation still needs a deployment
  decision and integration evidence.
- **Postgres/restore:** exercise Alembic upgrades twice, existing-state migration,
  pool limits, concurrent sessions, lease expiry, backup restore and epoch-floor
  fencing. SQLite tests alone do not establish the production Postgres path.
- **Node drivers:** run the newer TGI/SGLang/llama.cpp/LMDeploy drivers against
  real images in the meridian-node repo. Record support per tested version.

Defer batched observation writes until a measured fleet load demonstrates a
pool/write bottleneck. Preserve projection freshness and fencing semantics when
designing that change.

## 4. RC and v1.0 gate

Freeze the exact candidate and rerun the real-engine gateway matrix, partner
configuration and current security deployment checklist. Produce an image scan
for that image digest; the existing v0.9.3 scan is historical evidence. Resolve
the differing severity thresholds in older gate docs before sign-off.

The partner must rerun the complete supported scenario, not merely sign the
maintainer's older lab report. Cover tenancy boundaries, budgets/cost scope,
PII behavior, upstream failures, streaming disconnects, reload and deployment
recovery. Update pitch claims to the tested capability set and obtain partner
and security acknowledgements referencing the RC tag/image digest.

Only then publish v1.0.0. This is a release verification milestone, not a reason
to add every optional feature below.

## 5. Later implementations, ordered by demonstrated need

| Capability | Start when | Design/acceptance requirement |
|---|---|---|
| Shared budget/rate-limit enforcement across gateway replicas | Partner needs multiple active gateway replicas | Atomic multi-scope reservations and actual-token reconciliation; concurrency tests prove no over-admission; explicit store-outage policy |
| Shared/durable cost reporting | Partner needs cross-replica finance exports | Idempotent usage events, stable attribution, reconciliation and restart/replay tests |
| Billing adapter | Ledger export is insufficient for a paying deployment | Durable idempotent outbox/retry; delivery outages do not block inference; billing math matches the ledger |
| Async batch API | Concrete offline workload and compatible engine | Persistent jobs, authorization, cancellation, quotas and restart-safe retries; separate OpenAI compatibility contract |
| Semantic caching | Repeated workloads justify it | Tenant-safe keying, invalidation, sensitive-data policy and measured correctness/cost tradeoff; opt-in |
| SSO/OIDC and richer authorization | Partner requires organizational login/governance | Explicit identity lifecycle and role model, revocation and endpoint authorization tests |
| Response PII scanning | Contract requires it | Define streaming behavior and latency budget before implementation; do not imply request scanning already covers outputs |
| Edge/multi-region, true P/D disaggregation, request migration | Multi-node evidence and engine contracts exist | Dedicated infrastructure/engine design and failure experiments; not part of the local KV routing milestone |

Offline licensing and prefix-cache tenant noncing also remain optional roadmap
items. Define a real deployment requirement before implementation. Every selected
item gets its own branch, PR, tests, documentation and reproducible validation.
