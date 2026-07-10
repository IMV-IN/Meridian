# Sovereign AI Gateway — System Design Document

> **Version:** 0.1 (Pre-Alpha Architecture)  
> **Audience:** Founding engineers, infrastructure leads, technical co-founders  
> **Classification:** Internal — Not for distribution

---

## 1. The Actual Problem We Are Solving (Constraints First)

Before any architecture decision, we need to be honest about what this system needs to do and—more importantly—what it does **not** need to do.

### 1.1 The Regulatory Reality

The mandate comes from three places:

- **RBI Circular (2022–2024):** Financial data, including transaction logs, KYC records, and inference inputs/outputs derived from customer data, must reside on Indian soil. Specifically, "critical payment data" cannot leave India even transiently. Azure OpenAI, which routes through Microsoft's US datacenters before hitting Indian regions, fails this because the model weights and serving infrastructure are ultimately US-operated.
- **DPDP Act (2023):** Digital Personal Data Protection Act mandates data fiduciaries (any company processing Indian citizen data) to adopt technical and organizational measures. Inference logs containing PII must be locatable, deletable, and auditable.
- **IRDAI IT Framework (2024):** Insurance regulatory data cannot be processed by non-Indian entities without explicit board-level approval and periodic audits. In practice, this means on-soil compute is the only viable path.

What this means for us: **We are not a convenience product. We are a compliance product.** Companies will pay a 2–3× premium not because our models are better but because using anything else is regulatory risk. This fundamentally shapes every product and architectural decision.

### 1.2 Scale — Being Honest About the Numbers

This is where most system design documents get dangerously optimistic. Let us be realistic.

**On-prem deployment (single enterprise):**

Consider a large Indian bank — say, a mid-sized private bank with 20,000 employees. Of those:
- Knowledge workers (analysts, managers, officers): ~8,000
- Staff who would realistically use an LLM tool daily: ~3,000
- Concurrent users at peak (lunch hour or post-morning meeting): ~300
- Simultaneous in-flight LLM requests at true peak: **50–150 requests**

At 150 concurrent requests to a 30B model (Sarvam-1 class), each generating ~300 tokens at ~80 tokens/second on a 4×H100 node:
- Throughput needed: ~12,000 tokens/second
- A 4×H100 node running vLLM with continuous batching handles approximately 8,000–15,000 tokens/second for a 30B model depending on batch size and sequence length
- **Conclusion: 1–2 nodes covers a large private bank's entire AI workload.**

For SBI (250k employees), multiply by ~12 for a proportional estimate:
- True peak concurrent requests: ~1,500–2,000
- Nodes needed: 15–20 × 4×H100
- This is the upper ceiling for India's single largest employer using LLMs

**Our hosted (licensed) service — top 100 enterprise customers:**

India's top 100 companies by employee count are dominated by IT services firms (TCS, Infosys, Wipro), banks (SBI, HDFC, ICICI), and government PSUs. Realistically, in the first 3–4 years, our "top 100" will be more like 20–30 enterprises in BFSI and government.

Assuming 30 enterprise clients, each with 5,000 active LLM users:
- Total active users: 150,000
- True concurrent at peak: ~2,000–4,000 requests (1.5–2.5% of active users)
- Tokens/second needed: ~200,000–400,000
- GPU nodes (4×H100, 30B model): 20–50 nodes

**This is not a hyperscaler problem. This is a distributed systems problem with strict latency and isolation requirements.**

The maximum plausible load this system will ever face in India, even with aggressive adoption, is in the range of **100,000 tokens/second**. For context, that is approximately what a medium AWS region handles in a few seconds. We are not building for internet scale — we are building for sovereign enterprise scale, with the complexity coming from isolation, compliance, and multi-tenancy, not raw throughput.

---

## 2. Entities and Their Relationships

```
┌─────────────────────────────────────────────────────────────┐
│                    SOVEREIGN AI GATEWAY                      │
│                  (Us — The Orchestration Layer)              │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────┐          ┌──────────────────────────┐
│   Model Vendors      │          │   Compute Partners       │
│                      │          │                          │
│ • Sarvam AI          │          │ • Yotta (Mumbai/Noida)   │
│   (30B, 105B, Hindi) │          │ • NxtGen (Bangalore)     │
│ • Meta Llama (OSS)   │          │ • CtrlS (Hyderabad)      │
│ • Mistral (OSS)      │          │ • STT (Govt. adjacent)   │
│ • Future: Krutrim,   │          │ • Future: MeitY infra    │
│   iGPT, etc.         │          │                          │
└──────────────────────┘          └──────────────────────────┘
               │                              │
               └──────────────┬───────────────┘
                              │
                              ▼
                  ┌──────────────────────┐
                  │   Enterprise Client  │
                  │                      │
                  │ Mode A: Licensed     │
                  │ (we manage compute)  │
                  │                      │
                  │ Mode B: On-Prem      │
                  │ (they own infra,     │
                  │  we provide SW)      │
                  └──────────────────────┘
```

**Our role is the connective tissue.** We do not own GPUs (in the licensed model, we lease capacity). We do not train models. We provide:
1. The software layer that sits between enterprise users and inference backends
2. The trust layer: RBAC, audit, PII redaction, compliance logging
3. The management plane: provisioning, billing, monitoring, model lifecycle

---

## 3. Deployment Modes — Deep Dive

### 3.1 On-Prem Mode

The enterprise owns the hardware (or colocates at a datacenter) and runs our software stack inside their network perimeter.

**What we ship:**
- A container image bundle (OCI-compliant)
- A Helm chart or Docker Compose stack for non-Kubernetes environments
- A license key tied to their domain and hardware fingerprint
- An offline-capable model registry sync tool (pulls model weights once, serves forever without calling home)

**What connects to us:**
- License validation heartbeat (lightweight, can be air-gapped with annual key rotation)
- Telemetry (opt-in, anonymized, used for usage-based billing if applicable)
- Model registry pull (they pull new model weights on their schedule)

**Their data never leaves their perimeter.** Not even metadata about prompts. This is the non-negotiable contract for defense and government clients.

**On-prem stack layout:**

```
[Enterprise Network]
        │
   ┌────▼────────────────────────────────────────────┐
   │              Gateway Node (CPU-only)            │
   │  ┌──────────────┐  ┌───────────┐  ┌──────────┐ │
   │  │ Cloudflare   │  │ Auth &    │  │ Audit    │ │
   │  │ Tunnel /     │  │ RBAC API  │  │ Logger   │ │
   │  │ mTLS Proxy   │  │           │  │          │ │
   │  └──────────────┘  └───────────┘  └──────────┘ │
   │  ┌──────────────┐  ┌───────────┐               │
   │  │ PII Redact   │  │ Rate      │               │
   │  │ Middleware   │  │ Limiter   │               │
   │  └──────────────┘  └───────────┘               │
   └────────────────────┬────────────────────────────┘
                        │ Internal gRPC / HTTP/2
   ┌────────────────────▼────────────────────────────┐
   │           Inference Node(s) (GPU)               │
   │  ┌─────────────────────────────────────────┐    │
   │  │  vLLM Serving Engine                    │    │
   │  │  - Continuous batching                  │    │
   │  │  - KV cache management                  │    │
   │  │  - Tensor parallelism (multi-GPU)        │    │
   │  └─────────────────────────────────────────┘    │
   └─────────────────────────────────────────────────┘
   ┌─────────────────────────────────────────────────┐
   │           Observability Stack (optional)        │
   │  Prometheus + Grafana (fully local)             │
   └─────────────────────────────────────────────────┘
```

### 3.2 Licensed Mode (Managed Hosting)

The enterprise does not own hardware. They sign up through our portal, choose:
- Which datacenter partner (Yotta Mumbai, NxtGen Bangalore, etc.)
- Which model(s) to deploy
- Their team structure and access policies

We handle provisioning, deployment, scaling, and billing. Their API requests flow:

```
Enterprise App / User Browser
         │
    [Internet — TLS 1.3]
         │
    Cloudflare WAF + DDoS
         │
    Our Edge (Cloudflare Workers — routing, auth token validation)
         │
    [Private tunnel to Indian datacenter]
         │
    Our Inference Gateway (inside datacenter)
         │
    vLLM Inference Engine
```

**Why Cloudflare specifically:** Cloudflare has a presence in Mumbai, Chennai, and Delhi. Their Workers run at edge, meaning initial request validation (JWT decode, rate limit check, abuse detection) happens before traffic hits our datacenter-hosted inference backend. This cuts latency for auth overhead and provides DDoS protection without routing data through US infrastructure. Critically, Cloudflare's data processing agreements can be scoped to Indian points-of-presence — the actual inference payload (prompt content) never leaves our datacenter tunnel; only the auth headers are inspected at the edge.

---

## 4. Control Plane vs. Data Plane Separation

This is the most important architectural decision in the entire system.

```
┌──────────────────────────────────────────────────────────┐
│                     CONTROL PLANE                        │
│              (Cloudflare Workers + D1/KV)                │
│                                                          │
│  • Authentication & JWT validation                       │
│  • RBAC policy retrieval                                 │
│  • Rate limit counters                                   │
│  • Organization and team management                      │
│  • Billing events (counts, not content)                  │
│  • Model catalog and version management                  │
│  • Compute partner provisioning APIs                     │
│                                                          │
│  Lives: Cloudflare global network + Indian region KV     │
└──────────────────────────────┬───────────────────────────┘
                               │  Policy decisions only
                               │  No prompt/response content
                               ▼
┌──────────────────────────────────────────────────────────┐
│                      DATA PLANE                          │
│            (On Indian Datacenter Compute)                │
│                                                          │
│  • Actual prompt/response content                        │
│  • PII detection and redaction                          │
│  • Inference execution                                   │
│  • Audit logs (content-level)                            │
│  • KV cache state                                        │
│  • Model weights                                         │
│                                                          │
│  Lives: Yotta / NxtGen / customer on-prem               │
│  NEVER leaves Indian territory                           │
└──────────────────────────────────────────────────────────┘
```

This separation is what makes the Cloudflare choice defensible from a data residency standpoint. Cloudflare handles routing, authentication, and rate limiting — metadata operations. The actual conversation content flows through a private Cloudflare Tunnel (formerly Argo Tunnel) that terminates inside the Indian datacenter and never traverses Cloudflare's content inspection layer.

---

## 5. System Components — Low Level

### 5.1 API Gateway Layer (Cloudflare Workers)

Every enterprise client has a subdomain: `{org-slug}.api.sovereignai.in`

A Cloudflare Worker handles each inbound request:

```typescript
// Pseudocode — actual implementation in TypeScript
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // 1. Extract and validate JWT (RS256, signed by our control plane)
    const token = await validateJWT(request.headers.get('Authorization'), env.JWT_PUBLIC_KEY);
    if (!token) return new Response('Unauthorized', { status: 401 });

    // 2. Resolve org and team from token claims
    const { org_id, team_id, user_id, model_access } = token.claims;

    // 3. Rate limit check against Cloudflare KV (sliding window)
    const allowed = await checkRateLimit(env.KV, `rl:${org_id}:${team_id}`, limits);
    if (!allowed) return new Response('Rate limit exceeded', { status: 429 });

    // 4. Model access validation
    const requested_model = await getRequestedModel(request);
    if (!model_access.includes(requested_model)) {
      return new Response('Model not authorized for this team', { status: 403 });
    }

    // 5. Emit billing event (token count estimate from request body size)
    // This is an estimate only; actual count comes from inference backend
    await env.BILLING_QUEUE.send({ org_id, team_id, user_id, timestamp: Date.now() });

    // 6. Forward to inference backend via Cloudflare Tunnel
    const backendURL = await resolveBackend(env.KV, org_id, requested_model);
    const backendRequest = new Request(backendURL, {
      method: request.method,
      headers: addInternalHeaders(request.headers, { org_id, team_id, user_id }),
      body: request.body,
    });

    return fetch(backendRequest); // Streams response back to client
  }
}
```

Key properties of this layer:
- **Stateless:** No session state in the Worker. All state in KV or D1.
- **Fast path:** Auth + rate limit check completes in <5ms before any payload inspection.
- **Streaming-native:** The Worker streams the response body directly without buffering, enabling SSE token streaming to the client.
- **Content-blind:** The Worker never reads or logs the prompt or response content. Only metadata.

### 5.2 Inference Gateway (Rust — runs inside datacenter)

This is our core data-plane binary. It runs as a sidecar to the vLLM instances and handles everything content-related.

```
Incoming request (from Cloudflare Tunnel)
        │
   ┌────▼──────────────────────────────────────────────┐
   │           REQUEST PIPELINE                        │
   │                                                   │
   │  1. Parse OpenAI-compatible request body          │
   │  2. PII Detection (Presidio / custom NER model)   │
   │     → Replace entities with typed placeholders    │
   │     → e.g., "Rahul Sharma, PAN: ABCDE1234F" →     │
   │       "[PERSON_1], PAN: [PAN_1]"                  │
   │  3. Prompt injection detection (heuristic + ML)   │
   │  4. System prompt injection (org policy)          │
   │  5. Request audit log (redacted version)          │
   │  6. Route to vLLM via internal HTTP               │
   └───────────────────────────────────────────────────┘
        │
   ┌────▼──────────────────────────────────────────────┐
   │           RESPONSE PIPELINE                       │
   │                                                   │
   │  1. Stream tokens from vLLM (SSE)                 │
   │  2. Re-inject PII mapping into response           │
   │     (configurable: redact in logs, restore in     │
   │      response, or keep redacted in both)          │
   │  3. Token counting (exact, for billing)           │
   │  4. Response audit log                            │
   │  5. Stream to Cloudflare Tunnel → client          │
   └───────────────────────────────────────────────────┘
```

**Why Rust:** The gateway is on the critical latency path. Every millisecond added here degrades user experience. PII detection on a 2,000-token prompt should add <10ms overhead. Rust gives us zero-copy buffer handling, deterministic latency, and the ability to use fast C-library bindings for the NLP components without GC pauses. Python (even with asyncio) introduces GC pauses that are unacceptable in a streaming context.

**PII Detection Architecture:**

We run a two-stage detection:

Stage 1 (Fast, regex-based, <1ms): Aadhaar numbers, PAN, phone numbers, email addresses, bank account numbers. These patterns are deterministic and cover 80% of structured PII in Indian enterprise data.

Stage 2 (Slower, NER model, 5–15ms): Person names, organization names, location names in free text. We use a fine-tuned multilingual NER model (preferably Sarvam's own tokenizer-aware model for Indian names) running on CPU. We batch these across requests when possible.

For most requests, stage 1 suffices. Stage 2 is triggered by config per organization (some need full NER, some only need structured PII redaction).

### 5.3 Inference Engine Layer (vLLM)

We run vLLM in OpenAI API-compatible mode. This is not exotic — vLLM is production-grade, used at scale by virtually every company running open-weight models. The interesting decisions are around configuration.

**Per-tenant KV Cache Isolation:**

vLLM uses prefix caching. In a multi-tenant scenario, this creates a problem: if Tenant A's system prompt shares a prefix with Tenant B's, vLLM might reuse KV cache entries, creating a theoretical information leakage vector (not content leakage, but timing side-channels — an attacker might infer whether another tenant used a similar prompt prefix).

Our mitigation:
- Each tenant's system prompt is prepended with a tenant-unique nonce before being fed to vLLM
- This breaks prefix sharing across tenants while preserving intra-tenant prefix cache hits (same system prompt re-used within a company)
- The nonce is stable per-deployment but secret, so tenants cannot craft prompts to probe others' cache state

**Model Serving Configuration:**

For the 30B model (Sarvam-1 tier):
```yaml
# vllm serve config
model: /models/sarvam-30b
tensor_parallel_size: 2      # 2×H100 for 30B
max_model_len: 8192
gpu_memory_utilization: 0.90
enable_prefix_caching: true
max_num_seqs: 256            # max concurrent sequences
block_size: 16
```

For the 105B model (Sarvam-2 tier):
```yaml
model: /models/sarvam-105b
tensor_parallel_size: 4      # 4×H100 minimum for 105B
pipeline_parallel_size: 2    # across 8 GPUs for large deployments
max_model_len: 32768
gpu_memory_utilization: 0.92
enable_prefix_caching: true
max_num_seqs: 128
```

**Autoscaling (Licensed Mode):**

We use a custom autoscaler that watches vLLM's `/metrics` endpoint (Prometheus) for two signals:
- `vllm:num_requests_waiting` — requests queued waiting for a free sequence slot
- `vllm:gpu_kv_cache_usage_perc` — KV cache pressure

When `num_requests_waiting > 5` for more than 30 seconds, we trigger a scale-up. We provision a new vLLM instance, warm it up (model load takes 3–8 minutes for 105B), and add it to the upstream pool. We use a 15-minute cool-down before scaling down.

**Why not Kubernetes HPA:** The standard HPA reacts to CPU/memory metrics. GPU memory utilization and request queuing are better signals for LLM workloads. We write a custom HPA webhook or use KEDA (Kubernetes Event-Driven Autoscaling) with a custom scaler. In on-prem deployments without Kubernetes, we use Docker Swarm or plain Docker Compose with manual scaling driven by our monitoring stack.

### 5.4 Audit Logging Pipeline

This is a first-class citizen, not an afterthought. RBI and IRDAI auditors will request logs.

```
Inference Gateway
       │
       │  Structured log event (JSON)
       │  {
       │    timestamp, org_id, team_id, user_id,
       │    model, request_tokens, response_tokens,
       │    latency_ms, prompt_hash (SHA-256 of redacted prompt),
       │    pii_entities_detected: ["PERSON", "PAN"],
       │    pii_entities_count: 3,
       │    finish_reason,
       │    redacted_prompt_excerpt (first 200 chars, redacted)
       │  }
       │
   ┌───▼──────────────────────────────────────────────┐
   │            Audit Log Writer                      │
   │   • Append-only write to local disk buffer       │
   │   • Async flush to tamper-evident log store      │
   │   • Separate process, crash-safe ring buffer     │
   └───────────────────────────────────────────────────┘
       │
   ┌───▼──────────────────────────────────────────────┐
   │         Tamper-Evident Log Store                 │
   │                                                  │
   │  Implementation: Append-only object storage      │
   │  (MinIO in on-prem, or Yotta object store for    │
   │   licensed mode) + Merkle hash chain             │
   │                                                  │
   │  Each log batch gets:                            │
   │  - SHA-256 hash of content                       │
   │  - Hash of previous batch (chain)                │
   │  - Signed by our audit signing key (Ed25519)     │
   │                                                  │
   │  Auditors get a read-only key to verify chain    │
   └───────────────────────────────────────────────────┘
```

**Full prompt/response logging is opt-in and configurable per organization.** By default, we log only metadata and redacted excerpts. Full logging is available for organizations that require it (e.g., for internal model fine-tuning or compliance review) and is stored in their own on-soil storage with their own encryption keys (BYOK — Bring Your Own Key).

**Retention:** Default 90 days (configurable). BFSI clients often need 7 years under RBI record-keeping requirements. We support tiered storage (hot → warm → cold object storage) with lifecycle policies.

### 5.5 RBAC Model

The entity hierarchy:

```
Organization
    └── Department (e.g., "Risk Analytics", "Customer Support")
        └── Team (e.g., "Credit Risk Team", "Mumbai Branch Ops")
            └── User
            └── Service Account (for application integrations)
```

Each level can have policies:
- Which models are accessible
- Maximum token budget (per day, per month)
- Which PII policies apply (stricter policies override looser ones upward)
- Whether prompt caching is enabled
- Whether full audit logging is enabled

Policies cascade downward — an org-level "no customer PII in prompts" policy overrides any team-level setting.

**API Key Management:**

Organizations get a master key that can only be used to generate scoped child keys. Child keys carry JWT claims for `org_id`, `department_id`, `team_id`, and policy hash (fingerprint of the policies applying to this key at issuance time). The policy hash is validated at inference time against the live policy — if policies changed since key issuance, the key is valid but we re-fetch policies.

```
Master Key (never used for inference, only for provisioning)
    │
    ├── Department Key (scoped to department + its teams)
    │       │
    │       └── Team API Key (used by applications)
    │               │
    │               └── User Personal Key (for direct use)
    │
    └── Service Account Key (for CI/CD, automated pipelines)
```

---

## 6. Model Registry and Lifecycle Management

### 6.1 Model Catalog

We maintain a central model catalog that describes available models, their compute requirements, and compatibility metadata:

```json
{
  "model_id": "sarvam-2-105b-instruct",
  "vendor": "sarvam_ai",
  "display_name": "Sarvam-2 (105B)",
  "version": "1.2.0",
  "languages": ["hi", "en", "ta", "te", "kn", "ml", "bn", "gu", "mr", "pa"],
  "context_length": 32768,
  "compute_profile": {
    "minimum_gpus": 4,
    "recommended_gpus": 8,
    "gpu_type": "H100-80GB",
    "vram_required_gb": 210,
    "load_time_seconds": 480
  },
  "compliance": {
    "data_residency": "IN",
    "certifications": ["RBI_COMPLIANT", "DPDP_READY"],
    "model_card_url": "https://...",
    "license": "apache-2.0"
  },
  "artifact": {
    "registry": "registry.sovereignai.in",
    "image": "models/sarvam-2-105b:1.2.0",
    "sha256": "abc123...",
    "size_gb": 210
  }
}
```

### 6.2 Model Distribution for On-Prem

Model weights cannot be shipped via public internet (too large, security risk). We use a pull-based registry:

```
Customer On-Prem Network
        │
        │  model-sync agent (runs on their infra)
        │  - Authenticates with our registry using on-prem license key
        │  - Verifies SHA-256 of every layer before writing to disk
        │  - Supports resumable downloads (important for 210GB models)
        │  - Can operate over a VPN to our Indian datacenter registry
        │
    ┌───▼────────────────────────────────────────────┐
    │  Private Model Registry                         │
    │  (Hosted in India, OCI-compliant)               │
    │  Backed by our Yotta/NxtGen object storage      │
    └─────────────────────────────────────────────────┘
```

Model updates are versioned and non-destructive. The customer chooses when to upgrade. We support running multiple versions simultaneously (for A/B testing or gradual rollout).

### 6.3 Adding New Model Vendors

When a new model vendor (say, Krutrim or a future Indian LLM startup) wants to list on our platform:

1. They submit a model card and safety evaluation results to us
2. We run automated benchmarks (MMLU-Hindi, IndicGenBench, safety red-teaming)
3. We publish their model to our registry after signing the artifact
4. Customers can select it in the management console

This is an open platform commitment — we do not exclusive-lock with Sarvam. Sarvam gets preferred positioning because they are the only frontier-class option today and because their infrastructure partnership with IndiaAI Mission gives them access to compute we can leverage.

---

## 7. Cloudflare Integration — Detail

We use Cloudflare not just as a CDN/WAF but as the global control plane distribution layer.

### 7.1 Services Used

**Cloudflare Workers:** Edge compute for auth, routing, rate limiting. Runs in Mumbai and Chennai Cloudflare PoPs. Latency from Indian enterprise networks to Mumbai Cloudflare PoP: 5–15ms.

**Cloudflare KV:** Key-value store used for:
- Rate limit counters (per org, per team, per minute/hour/day)
- Active session tokens (short TTL — 15 minutes)
- Backend routing table (which org maps to which datacenter endpoint)

**Cloudflare D1:** SQLite-compatible SQL database at edge. Used for:
- Organization and team configuration (read-heavy, written rarely)
- Model access policies
- Billing plan limits

**Cloudflare Durable Objects:** Used for rate limiting coordination. Each org has a Durable Object that serializes rate limit increments, avoiding the race conditions of pure KV-based rate limiting at high concurrency.

**Cloudflare Tunnel (Zero Trust):** Private tunnel from our datacenter inference gateway to Cloudflare's network. The tunnel terminates in our datacenter; traffic flows inbound from Cloudflare Workers to our inference gateway without exposing any public IP. Our inference servers have no inbound firewall ports open to the internet.

```
Client → Cloudflare PoP (Mumbai) → [Cloudflare backbone] 
       → Cloudflare Tunnel endpoint → Our datacenter → vLLM
```

This also gives us DDoS protection for free — Cloudflare absorbs volumetric attacks before they reach our inference infrastructure.

### 7.2 What Cloudflare Never Sees

The Cloudflare Worker sees:
- HTTP headers (Authorization, Content-Type, org routing headers)
- Approximate request size (Content-Length)
- Destination path (/v1/chat/completions, /v1/embeddings, etc.)

The Cloudflare Worker does **not** see (because it streams the body directly without reading it):
- The prompt content
- The response tokens
- Any user data

This is verifiable in the Worker code — we can publish the Worker code as open source, allowing enterprise clients to audit that we never log their content at the edge.

---

## 8. Billing and Cost Attribution

### 8.1 Token Counting

Exact token counting happens in the Inference Gateway (data plane), not at the edge. The gateway intercepts the streaming response from vLLM, counts tokens as they stream, and emits a billing event at stream completion.

```
vLLM stream → Gateway counts tokens → emits BillingEvent {
    org_id, team_id, user_id,
    model_id, 
    prompt_tokens: 847,
    completion_tokens: 312,
    cached_tokens: 200,    // prompt tokens served from KV cache (billed at 0.1× rate)
    timestamp: ...,
    request_id: uuid
}
→ writes to local billing buffer (SQLite, on-disk)
→ async flush to billing aggregation service (every 60 seconds)
```

### 8.2 Pricing Model

**Reserved Capacity:** Enterprise signs a contract for N GPU-hours per month. This covers a baseline inference volume. Unused capacity does not roll over (capacity is provisioned, not consumed on demand).

**Per-token overage:** Requests beyond reserved capacity are billed at per-million-token rates. Input tokens and output tokens are priced differently (output is ~2–4× more expensive to generate).

**Cache discount:** Tokens served from prefix cache (KV cache hit) are billed at 10% of normal input token rate, incentivizing organizations to use consistent system prompts.

**Example pricing structure (illustrative, not final):**

| Tier | Reserved Capacity | Input price | Output price |
|------|------------------|-------------|--------------|
| Starter (30B) | 100M tokens/mo | ₹2/1K tokens | ₹8/1K tokens |
| Business (105B) | 500M tokens/mo | ₹5/1K tokens | ₹20/1K tokens |
| Enterprise | Negotiated | Volume discount | Volume discount |

### 8.3 Cost Attribution Dashboard

Every team lead and department head gets a dashboard showing:
- Token consumption by team, by user, by day
- Model distribution (which models consumed what)
- Estimated cost per business unit
- Anomaly alerts (a team suddenly consuming 10× normal — could be a runaway script)

This is the data that finance controllers actually care about. In large banks, AI spend accountability is becoming a board-level concern.

---

## 9. PII Redaction Pipeline — Deep Dive

### 9.1 Indian-specific Entity Types

Standard PII libraries (like Microsoft Presidio) are designed for Western data. We extend them:

| Entity Type | Pattern | Example |
|-------------|---------|---------|
| Aadhaar | 12-digit, validated with Verhoeff checksum | 2345 6789 1234 |
| PAN | [A-Z]{5}[0-9]{4}[A-Z] | ABCDE1234F |
| Voter ID | [A-Z]{3}[0-9]{7} | ABC1234567 |
| Passport | [A-Z][1-9][0-9]{7} | A1234567 |
| Indian mobile | +91 / 0 prefix + 10 digits | +91 98765 43210 |
| IFSC code | [A-Z]{4}0[A-Z0-9]{6} | HDFC0001234 |
| UPI ID | [a-z0-9.]{3,}@[a-z]{2,} | rahul@paytm |
| CIN | [A-Z]{1}[0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6} | Company registration |
| GST | 15-char format | 29ABCDE1234F1Z5 |

For name detection, we use a pre-trained NER model fine-tuned on Indian names (the model needs to recognize Rajesh, Subramaniam, Lakshmi, etc., which Western NER models frequently miss).

### 9.2 Policy-Driven Redaction Behavior

Organizations configure what happens with detected PII:

- **Block:** If PII is detected in a prompt, reject the request entirely and return an error. (High-security banking applications)
- **Redact-and-replace:** Replace PII with typed tokens, process the redacted prompt, restore in response. (Default for most enterprise use cases)
- **Redact-for-logs-only:** Process the prompt as-is (PII reaches the model), but store only redacted version in logs. (Lower-security use cases where PII context is needed for model quality)
- **Audit-only:** Detect and log PII presence but do not alter anything. (Initial rollout / assessment mode)

### 9.3 Performance Target

PII detection must add less than 20ms P99 latency to the request path for prompts up to 4,000 tokens. This is achievable with:
- Regex stage: <1ms
- NER stage on CPU (ONNX runtime, quantized model): 8–15ms for 4K tokens
- We skip NER on requests tagged with `pii_policy: structured_only` to save latency

---

## 10. Networking and Security Architecture

### 10.1 Network Topology (Licensed Mode)

```
[Enterprise AD/SSO]
        │ SAML 2.0 / OIDC
        ▼
[Our SSO Broker — Cloudflare Access]
        │ JWT issued
        ▼
[Cloudflare Worker — api.sovereignai.in]
        │ Private tunnel
        ▼
[Datacenter: Yotta Mumbai]
   ├── DMZ: Inference Gateway (Rust binary) — no internet access
   ├── Internal: vLLM instances — no internet access, no DNS resolution
   ├── Internal: Audit Log Store (MinIO) — no internet access
   └── Internal: Monitoring (Prometheus + Grafana) — no internet access

Egress from datacenter: ONLY outbound to Cloudflare Tunnel endpoint
Ingress to datacenter: ONLY from Cloudflare Tunnel
```

The inference nodes run with `--no-internet` equivalent network policies. A compromised vLLM instance cannot exfiltrate data because it has no outbound network path.

### 10.2 Encryption

- **In transit:** TLS 1.3 minimum everywhere. Internal datacenter traffic uses mTLS with short-lived certificates rotated every 24 hours.
- **At rest:** Model weights: AES-256 encrypted at the object storage layer. Audit logs: encrypted with org-specific key (BYOK via KMS). Billing data: encrypted with our master key.
- **Key management:** HashiCorp Vault (on-prem deployments) or Cloudflare KMS-backed secrets for licensed deployments.

### 10.3 Tenant Isolation (Licensed Mode)

In multi-tenant deployments on shared hardware, tenant isolation is provided at multiple levels:

**Process isolation:** Each organization gets dedicated vLLM worker processes. They do not share a process with other organizations, preventing in-process memory access.

**Network isolation:** Traffic to/from each org's vLLM workers is tagged at the gateway layer and routed through per-org virtual network interfaces (Linux network namespaces). Organizations cannot communicate with each other's inference processes.

**GPU isolation:** For premium enterprise tiers, organizations get dedicated GPU nodes. For standard tiers, they share GPU nodes with other organizations, but vLLM's request isolation (separate sequence queues per tenant namespace) and the KV cache nonce isolation described earlier provide separation.

**KV cache isolation:** The most subtle risk. We implement strict prefix isolation: each organization's KV cache is managed in a separate memory region within vLLM, and we patch vLLM's scheduler to never migrate sequence entries across organizational boundaries. This is an open-source contribution we will need to make.

---

## 11. Developer Experience and API Design

### 11.1 OpenAI API Compatibility

The inference API is fully OpenAI-compatible. Any application using OpenAI's Python SDK or REST API works by changing two lines:

```python
# Before (OpenAI)
client = OpenAI(api_key="sk-...")

# After (Sovereign AI Gateway)
client = OpenAI(
    api_key="sag-org_hdfc_bank-team_risk_analytics-...",
    base_url="https://hdfc-bank.api.sovereignai.in/v1"
)
# Everything else is identical
```

This eliminates migration friction. The API key encodes the org, team, and policies, so routing and authorization are transparent to the application.

### 11.2 Extended API (Our Value-Add)

Beyond OpenAI compatibility, we expose additional endpoints:

```
POST /v1/chat/completions          # Standard inference (OpenAI-compatible)
POST /v1/embeddings                # Standard embeddings
POST /v1/batch                     # Async batch inference (for bulk processing)

GET  /v1/usage                     # Token usage for current billing period
GET  /v1/usage/breakdown           # Per-user, per-team breakdown
GET  /v1/audit/logs                # Audit log retrieval (with pagination)

GET  /v1/models                    # Available models for this org
GET  /v1/health                    # Inference backend health
GET  /v1/metrics                   # Prometheus-compatible metrics endpoint
```

### 11.3 Management Console

The web console (built on Cloudflare Pages + Workers) provides:

**For IT admins:**
- Team and user management (with SSO integration)
- API key generation and rotation
- Model selection and deployment management
- PII policy configuration
- Usage dashboards and cost attribution

**For compliance officers:**
- Audit log search and export (with date range, user, team filters)
- PII detection event summaries
- Compliance report generation (RBI audit format, IRDAI format)

**For data center operators (our partners):**
- Node health and utilization
- Capacity planning data

---

## 12. Open Source Strategy

The long-term plan is to open source the core components. Here is how we think about the split:

**Open Source (MIT or Apache-2.0):**
- Inference Gateway (Rust) — core request/response pipeline
- PII detection engine with Indian entity types
- vLLM configuration templates and operational runbooks
- Audit log format specification
- Cloudflare Worker for routing/auth (reference implementation)

**Open source builds trust with enterprise security teams** who need to audit what is running inside their network. It also builds a community around Indian-language AI infrastructure.

**Proprietary (closed source):**
- Management console and billing system
- Compute partner integrations and provisioning automation
- SLA monitoring and alerting infrastructure
- Model registry and distribution network
- Enterprise support tooling

**Why this split works:** Enterprise customers pay for the management layer, the compliance guarantees, the SLAs, and the support. The core gateway being open source does not threaten revenue — it builds credibility and accelerates adoption.

---

## 13. Phased Delivery Plan

### Phase 0 — MVP (Month 1–3)

Goal: One design partner (a cooperative bank or insurance company) running on our stack in a single datacenter.

Deliver:
- Basic Inference Gateway in Rust (no PII redaction yet, basic auth)
- vLLM deployment with Sarvam-30B
- OpenAI-compatible API
- Simple API key management
- Token usage logging to flat files

**Do not build:** Multi-tenancy, autoscaling, billing, audit UI. Talk to the customer, understand their real workflow.

### Phase 1 — Beta (Month 3–6)

Goal: 5 enterprise customers, production-grade core.

Deliver:
- Full PII redaction pipeline
- RBAC with team hierarchy
- Tamper-evident audit logs
- Cloudflare-based routing and rate limiting
- Management console (basic)
- 105B model support

### Phase 2 — Scale (Month 6–12)

Goal: 20 enterprise customers, on-prem mode, multi-datacenter.

Deliver:
- On-prem deployment package (Helm chart)
- Multi-datacenter routing (Yotta + NxtGen)
- Advanced billing and cost attribution
- SSO integration (SAML 2.0)
- Open source release of core components

### Phase 3 — Platform (Month 12–24)

Goal: Marketplace. Third-party models. 100+ enterprise customers.

Deliver:
- Model vendor marketplace
- Multi-model routing (route to best model by query type)
- Fine-tuning pipeline integration
- Full compliance report generation

---

## 14. Infrastructure Cost Model

For a design partner's on-prem deployment running Sarvam-30B:

| Component | Spec | Approx. Cost (INR/month) |
|-----------|------|------------------------|
| Inference Node | 2×H100 80GB, 256GB RAM, 2×100GbE | ₹8–12 lakhs (leased from Yotta) |
| Gateway Node | 32-core CPU, 128GB RAM | ₹80,000 |
| Storage | 10TB NVMe (model + logs) | ₹40,000 |
| Network | 1Gbps dedicated | ₹30,000 |
| **Total** | | **₹9–13 lakhs/month** |

Our license fee sits on top of this. For a 30B deployment serving 5,000 active users, a ₹2–3 lakh/month software license fee is defensible (20–25% of total stack cost).

For the customer, the alternative is an OpenAI enterprise contract at roughly the same token volumes — which carries regulatory risk and is now being scrutinized by Indian regulators. The ₹10 lakh/month total cost is a bargain compared to regulatory fines or the cost of a compliance failure.

---

## 15. Key Open Questions for the Team

1. **vLLM multi-tenant KV cache isolation:** How much engineering effort is needed to safely isolate KV cache across tenants on shared GPU hardware? This could be a 2–3 month research spike and will determine whether we can offer shared GPU tiers.

2. **Sarvam partnership terms:** Do we get preferential access to model weights before public release? Model exclusivity (even 30-day) could be a meaningful competitive moat in the first 2 years.

3. **Compute partner SLAs:** Yotta and NxtGen do not have GPU infrastructure SLAs comparable to AWS. We need contractual commitments on hardware availability before we can commit enterprise SLAs to customers.

4. **Offline license validation:** For defense and government clients who need truly air-gapped deployments, how do we enforce license terms without a network callhome? Options: TPM-based hardware attestation, annual license key rotation with long-lived offline tokens.

5. **Fine-tuning as a service:** Several banks will want to fine-tune on their own historical data. This requires secure data pipelines into a training cluster. Is this in scope for Year 1 or a later addition?

---

*End of Document — v0.1*

*This document should be treated as a living specification. All architectural decisions are provisional until validated with a design partner customer.*