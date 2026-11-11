# Meridian Compute: GPU marketplace and jobs

Meridian Compute runs on top of Meridian Cloud ([CLOUD.md](./CLOUD.md)). It adds three things to the GPU fleet a workspace already manages:

- **Run jobs** on your own nodes: training runs, batch work and model services, as containers.
- **Rent GPUs** from other workspaces on the marketplace, billed per second.
- **List your GPUs** on the marketplace and earn from capacity you are not using.

It is built for Indian data centres, startups and research institutes: prices and bills are in rupees.

> **Status.** New in this release and not yet covered by the v1.0 gate
> ([internal/V1_GATE.md](./internal/V1_GATE.md)). Read [What it does not do yet](#what-it-does-not-do-yet)
> before offering it to a customer.

## Who uses it

| You are | You do | You pay |
| --- | --- | --- |
| A company with its own GPU servers | Enroll the servers as nodes and run jobs or models on them | Nothing |
| A startup or lab without enough GPUs | Rent a listing and run jobs on it | The provider's price plus a 2% Meridian fee |
| A data centre with spare GPUs | List nodes at a price you set | Nothing; you earn the full price |
| A research institute | Run one job across several nodes, with each replica told its rank and peers | Nothing on your own nodes |

## Pricing

A listing has a price per GPU-hour, set by the provider. A renter gets every GPU in the listing and pays:

```
provider price × GPUs × time  +  2% Meridian fee
```

- Billing is **per second**, from the moment you rent until you release.
- If the node stops reporting, the rental ends with reason "node lost" and you are billed only up to the node's last contact.
- You pay while the rental is held, whether or not a job is running.
- The provider is paid the full price. The fee is on top, paid by the renter.
- A rental keeps the price it started at; a later price change applies to new rentals only.
- Money is stored as whole paise and the ledger is append-only, so every bill can be re-added by hand.

Example: 2 GPUs at ₹120 per GPU-hour for 30 minutes costs ₹120.00 plus a ₹2.40 fee.

Credit is **prepaid**. Renting needs one hour of credit up front. If credit runs out, the rental ends by itself and its jobs stop; the overdraft is at most one background pass (30 seconds by default).

The fee is set with `MERIDIAN_CONTROL_MARKET_FEE_BPS` (default `200`, which is 2%).

## Set up a node to run jobs

Jobs are off by default. The host's owner turns them on in the agent's config, which is the host's own safety policy and cannot be overridden from the cloud:

```yaml
mode: managed-host
policy:
  management_enabled: true
  jobs_enabled: true
  # Either allow specific images...
  allowed_job_image_digests:
    - "sha256:<64 hex>"
  # ...or accept any digest-pinned image (what a marketplace host does).
  job_allow_any_image: false
  allowed_job_registries: [docker.io, ghcr.io]   # optional
  job_port_range: [9000, 9100]
  job_max_runtime_seconds: 0                     # 0 = no host-side ceiling
  device_capacity_bytes:
    "GPU-<uuid>": 85899345920
  engine_host: 10.0.0.5                          # address other nodes and gateways reach
```

`engine_host` must be an address the other nodes can reach if you run multi-node jobs. `127.0.0.1` only works for single-node jobs, because replicas use it to find each other.

A job container gets only the GPUs it was assigned and, if it asked for one, a single published port. It runs with no host mounts, all capabilities dropped, no privilege escalation and a process limit.

## Run a job

In the console, open **Jobs → Run a job**. From a terminal:

```bash
export MERIDIAN_CLOUD_URL=https://<your-cloud>
export MERIDIAN_WORKSPACE=ws_...
export MERIDIAN_API_KEY=mck_...      # Jobs → New API key

meridian compute run --follow \
  --image docker.io/pytorch/pytorch@sha256:<digest> \
  --gpus 1 -e WANDB_MODE=offline -- python train.py --epochs 3
```

- The image must be pinned by digest. A tag can change after you submit; a digest cannot. Get it with `docker inspect --format '{{index .RepoDigests 0}}' image:tag`.
- With no free GPU the job waits in the queue.
- `--follow` waits for the job and prints the last 8 KB of each replica's output. Write full logs and checkpoints to your own storage from inside the job.
- A job is launched at most once. If a node cannot prove whether a launch happened (the agent crashed mid-launch), the job fails with `LaunchUncertain` instead of silently running twice.

### Train across several nodes

```bash
meridian compute run --replicas 4 --gpus 8 --image <image> -- \
  torchrun --nnodes={{replicas}} --node_rank={{rank}} --master_addr={{master}} train.py
```

All replicas are placed or none are. Each replica gets:

| Variable | Value |
| --- | --- |
| `MERIDIAN_JOB_RANK`, `MERIDIAN_JOB_REPLICAS` | This replica's index and the total |
| `MERIDIAN_JOB_PORT` | The port published for this replica |
| `MERIDIAN_JOB_PEERS` | `host:port` of every replica, in rank order |
| `MERIDIAN_JOB_MASTER` | `host:port` of rank 0 |

The same values are available in the command and in environment values as `{{rank}}`, `{{replicas}}`, `{{port}}`, `{{peers}}` and `{{master}}`. If one replica fails, the job fails and the others are stopped.

### Run a model as a service

```bash
meridian compute run --service --name llama --image <vllm image> -- \
  meta-llama/Llama-3.1-8B-Instruct --port {{port}} --api-key {{service_token}}
```

The job page shows the endpoint and the bearer token the service was told to require, with a `backends:` snippet for a Meridian gateway's `config.yaml`.

## Templates

You do not have to write an image digest and `torchrun` flags for a first job. In the console, **Jobs → Run a job** starts with a picker: choose a template and every field is filled in (and stays editable), or choose **Custom**. From a terminal:

```bash
meridian compute templates
meridian compute run --template python-script --follow
meridian compute run --template pytorch-ddp --follow
meridian compute run --template jupyterlab --name notebook      # then open the job's page
meridian compute run --template python-script --follow -- python train.py   # your own command
```

Over the API, `GET /api/w/{ws}/job-templates` lists them (viewer and up), and `POST /api/w/{ws}/jobs` accepts `"template": "<id>"`. The template's `kind`, `image`, `args`, `env`, `gpus_per_replica`, `replicas` and `timeout_seconds` are the defaults; any of those you also send replaces the template's value as a whole (sending `env` replaces the template's `env`, it does not merge). The merged job is validated like any other, and an unknown template id is a 400.

| Template | Type | What it runs | After it starts |
| --- | --- | --- | --- |
| `jupyterlab` | service, 1 GPU, 8 h limit | JupyterLab with PyTorch and CUDA from the Jupyter Docker Stacks (`quay.io/jupyter/pytorch-notebook`), listening on `{{port}}` and requiring `{{service_token}}` | Open the job page and use the notebook link, `<endpoint>/lab?token=<token>`. Files are lost when the job ends |
| `pytorch-ddp` | job, 2 replicas × 1 GPU, 15 min limit | `torchrun` on the official `pytorch/pytorch` image: rendezvous on `{{master}}` (c10d), then a tiny script in which every replica all-reduces and prints its rank and the group size | Each replica's output shows `all_reduce sum=3 (expected 3)`. Replace the last two arguments (`-c` and the script) with your own command to train |
| `vllm-server` | service, 1 GPU, 8 h limit | The official `vllm/vllm-openai` image serving `Qwen/Qwen2.5-1.5B-Instruct` (fits one 24 GB GPU) with `--port {{port}} --host 0.0.0.0 --api-key {{service_token}}` | The first start downloads the model. Then `curl <endpoint>/v1/models -H "Authorization: Bearer <token>"`, or use the `backends:` snippet on the job page to add it to a gateway. Change the first argument (the model name) for another model |
| `python-script` | job, 1 GPU, 15 min limit | `python -c "import torch; print(torch.cuda.get_device_name(0))"` on `pytorch/pytorch` | The replica's output is the GPU's name. Replace the arguments with your own command |

The 8 hour limit on services stops a forgotten notebook from holding a GPU for ever; clear **Stop after** in the form (or send `timeout_seconds: 0`) for none. The `pytorch-ddp` template runs its script under `torchrun --no-python`, so it needs no code in the image; `torchrun` prints a harmless warning that `--node-rank` is ignored with the c10d backend, because the ranks are assigned at rendezvous. Hosts must set `policy.job_allow_any_image` or list the template digests in `allowed_job_image_digests`, and `engine_host` must be reachable between nodes for `pytorch-ddp`.

### Digests are pinned; bumping them

Every template image is pinned by digest, the same rule as any other job. The templates live in `meridian_control/templates.py`, and the comment above each digest records the tag it was resolved from:

| Constant | Resolved from | Digest covers |
| --- | --- | --- |
| `PYTORCH_IMAGE` | `docker.io/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` | linux/amd64 only (the publisher ships no other platform for CUDA images) |
| `JUPYTER_IMAGE` | `quay.io/jupyter/pytorch-notebook:cuda12-2026-11-10` | multi-arch index (amd64, arm64) |
| `VLLM_IMAGE` | `docker.io/vllm/vllm-openai:v0.19.1` | multi-arch index (amd64, arm64) |

To bump one, resolve the new tag to its digest, put it in the constant with its tag in the comment, and run `pytest tests/control/test_job_templates.py`:

```bash
docker buildx imagetools inspect docker.io/vllm/vllm-openai:v0.19.2 --format '{{.Manifest.Digest}}'
```

The test checks that every template image is digest-pinned and that every template passes job validation. It does not pull the images, so run each template once on a GPU node after a bump.

## Rent GPUs

```bash
meridian compute offers
meridian compute run --offer off_... --follow --image <image> -- python train.py   # rent for this job only
meridian compute rent off_...        # or hold it until you release
meridian compute release rnt_...
meridian compute balance
```

`run --offer` rents the listing, runs the job and releases the rental when the job ends, so you pay for the job's duration only. Renting needs the admin role, because it spends the workspace's credit.

## List your GPUs

In the console, open **Marketplace → List a node**. The node must be online with `policy.jobs_enabled: true`, and its GPUs must be idle: stop any engines or jobs using them before you list. The listing shows the GPU model and memory the node itself reports.

- While a node is listed, your own new jobs stay off its GPUs, so a renter never finds them busy. Pause the listing to use them yourself.
- You cannot publish an engine onto a listed or rented GPU. If a listed GPU becomes busy anyway, the listing shows "in use by the provider" and cannot be rented until it is idle.
- You can end a rental (for maintenance). The renter's jobs fail and you are paid for the time used.
- **You cannot see what a renter runs.** The node page shows that a GPU is taken and nothing else: not the image, arguments, environment or logs.

## Share with partner institutes

A consortium or a pair of partner universities can share GPUs without a public market.

In the console, **Marketplace → List a node**, choose **Private: only workspaces you name** and type the partners' workspace slugs, separated by commas (a slug is shown on each workspace's Settings page). Over the API, `POST /api/w/{ws}/market/offers` and `PATCH /api/w/{ws}/market/offers/{id}` take `visibility` (`"public"` or `"private"`, default public) and `shared_with` (a list of slugs; a workspace id also works).

- A private listing is listed to, and rentable by, only the workspaces in `shared_with` and its owner. Everyone else gets `404 listing not found` when they try to rent it, the same answer as for an id that does not exist, and a job that asks to run on it (`offer_id`) gets the same 404.
- A slug that matches no workspace is refused with `400 UNKNOWN_WORKSPACE` naming it. Slugs are lower-cased, de-duplicated, and your own is dropped. Each partner is **stored by its workspace id**, not its slug: if a partner's slug ever changes, the listing stays shared with that workspace (and is shown with its current slug), and a workspace that later takes the old slug gets nothing. An edit that does not send `shared_with` keeps the current partners. A partner sees `visibility: "private"` on the listing (the console shows "Shared with you") but not who else it is shared with; only the owner sees `shared_with`.
- A private listing may be priced at **₹0** to share it free. A public listing keeps the ₹1 minimum, including when a free private listing is made public, which needs a price. A price is always required: a missing one is an error, never "free".
- A free rental needs no credit, posts **no ledger rows** (no zero-amount rows), and is never ended for lack of credit. It still holds the GPUs exclusively until released, and the provider can still end it. If a workspace runs out of credit, its paid rentals end and its free ones keep running.
- **Removing a partner ends its rental.** When an edit leaves a workspace that is renting the listing off `shared_with`, its rental ends at once with `end_reason: "sharing_ended"` (billed up to that second if it was paid), its jobs on the node stop and are marked failed, and it can no longer rent the listing or run jobs on the old rental (404). Adding partners ends nothing.
- **A public listing never keeps a free rental.** Making a free private listing public needs a price of at least ₹1, and a ₹0 rental that is running on it ends the same way (`sharing_ended`). A paid rental by a partner continues at the price it started at, since the partner can still rent a public listing.
- Pausing a listing does not end a rental; it only stops new ones.
- The console shows a free price as "Free", and the CLI as `free`.

## Public listings feed (for a website)

`GET /api/public/offers` needs no sign-in and lists what is on the marketplace, for a website or a status page:

```json
{
  "offers": [{
    "title": "2× H100, Mumbai", "region": "Mumbai", "gpu_model": "NVIDIA H100", "gpu_count": 2,
    "vram_bytes_per_gpu": 85899345920, "price_paise_per_gpu_hour": 10000, "hourly_total_paise": 20400,
    "available": true, "provider_name": "Mumbai DC"
  }],
  "total_offers": 1,
  "summary": {
    "available_gpus": 2, "gpus_by_model": {"NVIDIA H100": 2}, "regions": ["Mumbai"],
    "lowest_price_paise_per_gpu_hour": 10000
  }
}
```

- Only **public, listed** offers appear. Private, paused and removed listings never do. A rented or offline listing stays in `offers` with `available: false`.
- `hourly_total_paise` is the whole listing per hour with the Meridian fee, as a renter would pay it. Money is integer paise.
- `summary` counts **available** listings only: `available_gpus`, GPUs per model, the regions they are in, and the lowest price per GPU-hour (`null` when nothing is available). With nothing listed, `offers` is `[]` and the summary is zeros and empties.
- `offers` holds at most **100** listings, cheapest available first; `total_offers` is how many public listings there are, and `summary` covers all of them. No node, device, listing or workspace ids, and no emails, are included. `title`, `region` and `provider_name` are text the provider typed, so a website must escape them.
- The answer is built once and kept in memory for **30 seconds** (per control process), so a busy page cannot load the database, and a price change can take up to 30 seconds to show. The response carries `Cache-Control: public, max-age=30`; it is the only `/api` route that is not `no-store`.
- It exists only on a cloud-mode control plane (`MERIDIAN_CONTROL_CLOUD=1`). A standalone control plane answers 404. It sends no CORS headers, so call it from your server or from a page served by the cloud itself.

## GPU-hour usage and monthly quotas

A department head can see who used how much GPU time and cap it per person. This is for jobs on the workspace's own nodes; rented GPUs are already limited by prepaid credit.

```bash
meridian compute usage --month 2026-11          # per person, with group and quota
curl -H "Authorization: Bearer $MERIDIAN_API_KEY" \
  "$MERIDIAN_CLOUD_URL/api/w/$MERIDIAN_WORKSPACE/usage?month=2026-11&format=csv" > usage.csv
```

In the console, **Billing → GPU-hour usage** has a month picker, the per-person table, group totals and a CSV download.

**Who sees what.** Owners and admins (and admin API keys) see every person and group (`"scope": "workspace"`). Operators and viewers see only their own row and the workspace totals (`"scope": "self"`, `groups: []`, since a group can be one person); a non-admin API key sees the row of the member who created it. The CSV follows the same rule.

**How usage is counted**

- GPU-seconds for a replica = GPUs × (finished − started). A replica that is still running counts up to now, and one that never started counts nothing. Hours are shown to two decimals.
- A month is a **UTC calendar month**. A job that spans midnight on the 1st is split between the two months.
- A job counts for the member who submitted it. A job submitted with an API key counts for the member who created the key. This is **recorded on the job when it is submitted** (`attributed_to`), so creating, renaming or revoking keys later never moves hours between people or off anyone's quota. A key created by an admin API key belongs to the member behind that key. (Jobs from before this was recorded are attributed by key name: to the creator if exactly one person made keys with that name, otherwise to `key:<name>`.)
- **Own** usage is on nodes the workspace owns. **Rented** usage is a job pinned to a rental, or any replica placed on a node the workspace does not own.
- The report lists every member (including those with no usage) and anyone who used GPUs but has since left. Each row has `gpu_hours_own`, `gpu_hours_rented`, `jobs` (jobs that started), `quota_gpu_hours` and `remaining_gpu_hours`; the response also has per-group and workspace totals. The CSV has the per-person rows. Names that start with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet does not run them.

**Quotas**

- An admin sets a person's **group** (up to 64 characters, for example `vision-lab`) and **monthly GPU-hour quota** under **Settings → Members → Limits**, or with `PATCH /api/w/{ws}/members/{user_id}/compute` `{"group": "...", "gpu_hour_quota": 40}`. A workspace default applies to members with no quota of their own: Settings → Members, or `PATCH /api/w/{ws}/compute-settings` `{"default_gpu_hour_quota": 20}`. Empty or `null` means unlimited. A quota of `0` closes the workspace's own nodes to that person.
- Owners and admins are not exempt: they are limited by their own quota, or by the default if they have none. There is no way to exempt one member from a default; leave the default empty and set quotas individually.
- When a member's own-node usage this month has reached the quota, a new job is refused with `409 QUOTA_EXCEEDED`, saying what was used and when the quota resets (the first of next month, UTC). A job pinned to a rental or a listing (`rental_id`, `offer_id`) is not checked: it runs on another workspace's GPUs (paid with credit, or free from a partner) and cannot reach your own nodes. A template is applied, and the merged job validated, before the quota check, so a template cannot get round it.
- A job with several replicas or several GPUs counts every GPU: 2 replicas × 4 GPUs for one hour is 8 GPU-hours.
- A job that was already queued when its submitter went over quota stays queued with "waiting: monthly GPU-hour quota used" and does not take a GPU on the workspace's own nodes. It can still be placed on rented GPUs, and it starts when the quota is raised or the month rolls over.
- **Running jobs are never stopped.** A long job can take a person past their quota; the quota stops the next job, not this one.
- **At the boundary.** Quotas are checked, under the scheduler's lock, when a job is submitted and when a queued job is considered (every background pass). The check counts hours already used, not hours a job will use, so a member below quota can still start a job that takes them over it, and a job that is already running when the quota is reached, or when the month ends, runs on. Its hours count in the month they are used. A member can therefore exceed a quota by what their running jobs use; an admin who needs a hard stop cancels the job.

## Roles

| Action | Viewer | Operator | Admin |
| --- | --- | --- | --- |
| See jobs, listings, billing | ✓ | ✓ | ✓ |
| Run and cancel jobs, read a service's token | | ✓ | ✓ |
| See your own GPU-hour usage and the workspace total | ✓ | ✓ | ✓ |
| See everyone's GPU-hour usage and group totals | | | ✓ |
| Set groups and quotas, rent, list (public or private), create API keys | | | ✓ |

API keys (`mck_…`) act in one workspace with the role you choose. They cannot manage members. A key's jobs count for the member who created it, against that member's quota.

## Adding credit and paying providers

Meridian Compute does not take card or UPI payments. The people who operate the cloud (the emails in `MERIDIAN_CONTROL_ADMIN_EMAILS`) record money that moved outside it, on the **Billing** page or over the API:

```bash
POST /api/platform/credits   {"workspace": "<id or slug>", "amount_paise": 2500000, "reference": "<UTR>"}
POST /api/platform/payouts   {"workspace": "<id or slug>", "amount_paise": 1000000, "reference": "<NEFT ref>"}
GET  /api/platform/summary
```

A payment reference can be credited only once, and a payout reference can be used only once.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `MERIDIAN_CONTROL_MARKET_FEE_BPS` | `200` | Fee on top of the provider's price, in basis points |
| `MERIDIAN_CONTROL_METER_INTERVAL` | `600` | Seconds between ledger postings for a running rental |
| `MERIDIAN_CONTROL_COMPUTE_TICK` | `30` | Seconds between background passes (metering, credit, timeouts, queue) |
| `MERIDIAN_CONTROL_NODE_LOST_SECONDS` | `300` | Silence after which a node's running replicas are declared lost |

Upgrading an existing cloud: run `meridian-control migrate`. The migrations only add tables and columns; existing listings stay public.

## What it does not do yet

- **Payments and tax.** No payment gateway, no GST invoices, no automatic payouts. Credits and payouts are recorded by hand against a reference.
- **Networking between organisations.** A service or a multi-node job is reachable only where the host's network allows. Meridian does not tunnel between a renter and a provider, and a gateway does not pick up a rented service automatically.
- **Secrets.** Job environment variables are stored as plain text. Do not put tokens in them.
- **Storage.** No managed volumes or datasets. A job brings and saves its own data.
- **Partial rentals.** A listing is rented whole, to one renter at a time.
- **Partner groups.** A private listing names workspaces one by one; there are no consortium objects, invitations to accept, or per-partner prices.
- **Isolation.** A job is a hardened Docker container, not a virtual machine. A provider accepting arbitrary images should run on hosts it is prepared to treat that way.
- **Restarts.** A failed job or service is not restarted.
- **Multiple control replicas.** Metering and scheduling are serialized by an in-process lock, so run compute on one control replica. A multi-replica Postgres deployment needs a database advisory lock first.
- **Scale.** Scheduling is first-come-first-served with no priorities, and no limit beyond the GPU-hour quotas above and 200 unfinished jobs per workspace.
- **Usage history.** Usage is computed from job records each time it is asked for. There are no stored monthly statements, no per-group quotas, and no alerts when someone nears a quota.

## Validate it yourself

1. `pytest -q tests/control/test_compute.py` in this repo and `pytest -q tests/test_jobs.py` in `meridian-node`.
2. Start `meridian-control run` in cloud mode, enroll a node with `policy.jobs_enabled: true`, and run `meridian compute run --follow --image <digest-pinned image> -- echo hello`.
3. List the node from one workspace, credit a second, rent from the second, and check that the two balances and the platform fee add up to the credit.
4. Set a member's quota to 1 GPU-hour, run a job for longer than that, and check that the job keeps running, the next `meridian compute run` fails with `QUOTA_EXCEEDED`, and `meridian compute usage` shows the hours.
5. List a node as private, shared with a second workspace at ₹0. Check that a third workspace gets a 404 on it, that the second rents it with no credit, and that the ledger has no new rows.
6. `curl -i $MERIDIAN_CLOUD_URL/api/public/offers` with no cookie: public listings only, `Cache-Control: public, max-age=30`.
