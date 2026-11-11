# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the IT or HPC admin at an Indian research institute or university that already owns GPU servers. This person enrolls servers, runs the console, sets quotas and runs the institute's LLM endpoint.

Secondary users:

- Lab heads and PIs. They want their group's jobs to run and want to see GPU-hours per person and per group.
- Professors running courses. They give students GPU-hour quotas and a Jupyter template.
- Researchers and students. They submit jobs.

## Product Purpose

Meridian turns GPUs an institute already owns into a shared campus service. It has three parts: a campus GPU cloud, a campus LLM endpoint, and a marketplace for renting idle GPUs between workspaces.

Success means the admin can set it up on existing hardware, and labs and courses can then use the GPUs without the admin handling each request.

## Positioning

Meridian runs on GPUs the institute already owns. Datasets, checkpoints, weights, prompts and completions stay on campus.

Nodes and gateways only connect outward to the control plane. The cloud receives health, job specs, GPU-hour counts and the last 8 KB of job output. It never receives prompts or keys.

The control plane can be hosted (Meridian Cloud) or self-hosted on campus. The code is in the IMV-IN GitHub org.

## Operating Context

- The node agent `meridian-node` runs on each GPU server.
- The console is at `/ui`.
- The CLI is `meridian compute offers|rent|rentals|release|balance|jobs|run|logs|cancel|usage|templates`.
- The gateway is installed with `curl -fsSL <your server>/install.sh | sh`. The package is not on PyPI, so no page may show `pip install meridian-gateway`.
- Roles are viewer, operator and admin.
- API keys start with `mck_`.
- A public read-only listings feed exists at `GET /api/public/offers`.

## Capabilities and Constraints

### Campus GPU cloud (lead capability)

- Pools the institute's own GPUs for jobs, JupyterLab, PyTorch DDP across servers and vLLM model servers.
- Job templates: jupyterlab, pytorch-ddp, vllm-server, python-script.
- Reports GPU-hour usage per person and group. Admins see everyone; members see only their own row.
- Sets monthly GPU-hour quotas per member, with a workspace default.
- Quotas are soft. A running job is never stopped; the next job is refused or queued.

### Campus LLM endpoint

- An OpenAI-compatible gateway with per-key auth.
- Daily and monthly token budgets at org, team and user level.
- Model allow-lists and an India PII pack.
- Console sign-in can be limited to an email domain. This is set once per Meridian Cloud deployment, not per workspace.

### Marketplace

- Owners rent idle GPUs to other workspaces at the owner's price plus a 2% Meridian fee.
- Billing is per second, in INR, from prepaid credit.
- Owners can also share privately with named partner workspaces, optionally at ₹0.
- Partners must be on the same Meridian Cloud.

### Limits that must never be claimed otherwise

- No payment gateway. There is no card, UPI or GST invoicing; credit is added by the platform.
- A single control-plane replica. No high-availability claim.
- Jobs get container isolation only: no memory cap, default bridge network, and job env stored in plain text.
- No Slurm integration and no managed storage.
- Notebook access is only through the token-protected service link.
- Multi-node jobs need reachable engine hosts.
- No real-GPU benchmark has been published.

### Engineering rules

- Prompts are never logged.
- Prometheus labels stay bounded.
- OpenAI request and response shapes stay stable.

### Open decision

- Whether marketplace providers agree to be named publicly. The feed returns `provider_name`, and the founder has not approved naming providers.

## Brand Commitments

The name is Meridian (org IMV-IN). The voice is plain, specific and honest about limits. A "what it does not do yet" stance is welcome.

## Evidence on Hand

There is nothing external yet: no customers, logos, testimonials, benchmarks or pilot names. Future work must not invent any.

The site may show only:

- the product itself, including the console;
- the docs;
- real CLI output formats;
- clearly labelled sample data, such as "Physics Dept. (sample)" or example.ac.in.

## Product Principles

1. Show the real product, not claims. Use real screens, real CLI output and labelled samples.
2. Admin-first setup, with lab and teaching value on top. The admin gets it running; PIs and professors benefit from it.
3. Data stays on campus. Say what leaves campus and what does not, exactly.
4. Honest limits. State what Meridian does not do yet next to what it does.
5. India-first. Use INR and Indian PII handling, and write for Indian institutes.

## Accessibility & Inclusion

Target WCAG 2.1 AA. The console has an `npm run audit:ui` gate.
