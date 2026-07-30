<div class="mdx-hero" markdown>

# Meridian

## The gateway between your applications and your inference fleet.

Meridian is an OpenAI-compatible reliability and control plane for self-hosted
LLMs. Put it in front of vLLM, SGLang, TensorRT-LLM, Ollama, or any compatible
server. Keep application code unchanged while gaining health-aware routing,
failover, tenant boundaries, canary rollouts, budgets, and evidence-grade
operations.

[Run the five-minute quickstart](QUICKSTART.md){ .md-button .md-button--primary }
[See what shipped](ship.md){ .md-button }

</div>

<div class="mdx-signal-grid" markdown>

<div class="mdx-signal" markdown>
**01 / Drop in**

Point an existing OpenAI client at Meridian. Streaming and non-streaming
requests keep their familiar shape.
</div>

<div class="mdx-signal" markdown>
**02 / Stay reliable**

Health checks, failover, latency-aware routing, passive failure handling, and
canary rollback keep bad backends away from callers.
</div>

<div class="mdx-signal" markdown>
**03 / Know who uses what**

Dedicated tenant pools, API-key identity, per-key budgets, model limits, and
org-scoped usage give platform teams control without building a second API.
</div>

<div class="mdx-signal" markdown>
**04 / Keep proof**

Prometheus metrics and metadata-only logs make the fleet visible. Optional
tamper-evident audit blocks provide a stronger chain of custody.
</div>

</div>

## The shortest path to a running gateway

```bash
git clone https://github.com/IMV-IN/Meridian.git
cd Meridian
docker compose up --build
```

Then send the request your application already knows how to send:

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-model","messages":[{"role":"user","content":"Hello"}]}'
```

Look for `x-request-id` and `x-meridian-backend`. Open the operator view at
[`/ui`](http://localhost:8080/ui), then move to the [flagship Phase 3

## A control plane, not another model server

```text
Your application
      |
      | OpenAI-compatible HTTP / SSE
      v
  Meridian  --->  stable backends
      |\
      | \-------> canary / dedicated pools
      |
      +---------> metrics, usage, audit metadata
```

Meridian does not schedule GPU kernels or own a KV allocator. It makes the
servers you already operate safer to expose, easier to observe, and easier to
change.

## Choose your next page

| You need to... | Start with |
|---|---|
| Run it locally | [Quickstart](QUICKSTART.md) |
| Wire a real backend | [Deployment guide](DEPLOY.md) |
| Design tenant boundaries | [Configuration reference](CONFIGURATION.md) |
| Operate it in production | [Operations runbook](OPS_RUNBOOK.md) |
| Understand the release scope | [What's shipped](ship.md) |

## Project posture

Meridian is MIT-licensed, self-hosted, and currently focused on reliability,
visibility, and platform controls for inference fleets. Read the [security
policy](https://github.com/IMV-IN/Meridian/blob/main/SECURITY.md), [release
notes](https://github.com/IMV-IN/Meridian/blob/main/CHANGELOG.md), and [known
issues](KNOWN_ISSUES.md) before evaluating it for production.
