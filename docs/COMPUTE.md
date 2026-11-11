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
  --model meta-llama/Llama-3.1-8B-Instruct --port {{port}} --api-key {{service_token}}
```

The job page shows the endpoint and the bearer token the service was told to require, with a `backends:` snippet for a Meridian gateway's `config.yaml`.

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

In the console, **Marketplace → List a node**, choose **Private: only workspaces you name** and type the partners' workspace slugs, separated by commas (a slug is shown on each workspace's Settings page). Over the API, `POST /api/w/{ws}/market/offers` and `PATCH /api/w/{ws}/market/offers/{id}` take `visibility` (`"public"` or `"private"`, default public) and `shared_with` (a list of slugs).

- A private listing is listed to, and rentable by, only the workspaces in `shared_with` and its owner. Everyone else gets `404 listing not found` when they try to rent it, the same answer as for an id that does not exist, and a job that asks to run on it (`offer_id`) gets the same 404.
- A slug that matches no workspace is refused with `400 UNKNOWN_WORKSPACE` naming it. Slugs are lower-cased, de-duplicated, and your own is dropped. A partner sees `visibility: "private"` on the listing (the console shows "Shared with you") but not who else it is shared with; only the owner sees `shared_with`.
- A private listing may be priced at **₹0** to share it free. A public listing keeps the ₹1 minimum, including when a free private listing is made public, which needs a price. A price is always required: a missing one is an error, never "free".
- A free rental needs no credit, posts **no ledger rows** (no zero-amount rows), and is never ended for lack of credit. It still holds the GPUs exclusively until released, and the provider can still end it. If a workspace runs out of credit, its paid rentals end and its free ones keep running.
- Changing `shared_with` or making a listing public does not end a rental that is already running; it only decides who can rent next.
- The console shows a free price as "Free", and the CLI as `free`.

## GPU-hour usage and monthly quotas

A department head can see who used how much GPU time and cap it per person. This is for jobs on the workspace's own nodes; rented GPUs are already limited by prepaid credit.

```bash
meridian compute usage --month 2026-11          # per person, with group and quota
curl -H "Authorization: Bearer $MERIDIAN_API_KEY" \
  "$MERIDIAN_CLOUD_URL/api/w/$MERIDIAN_WORKSPACE/usage?month=2026-11&format=csv" > usage.csv
```

In the console, **Billing → GPU-hour usage** has a month picker, the per-person table, group totals and a CSV download. Everyone in the workspace (viewer and up) can read it.

**How usage is counted**

- GPU-seconds for a replica = GPUs × (finished − started). A replica that is still running counts up to now, and one that never started counts nothing. Hours are shown to two decimals.
- A month is a **UTC calendar month**. A job that spans midnight on the 1st is split between the two months.
- A job counts for the member who submitted it. A job submitted with an API key counts for the member who created the key, as long as exactly one person created keys with that name; otherwise it is listed under `key:<name>`.
- **Own** usage is on nodes the workspace owns. **Rented** usage is a job pinned to a rental, or any replica placed on a node the workspace does not own.
- The report lists every member (including those with no usage) and anyone who used GPUs but has since left. Each row has `gpu_hours_own`, `gpu_hours_rented`, `jobs` (jobs that started), `quota_gpu_hours` and `remaining_gpu_hours`; the response also has per-group and workspace totals. The CSV has the per-person rows. Names that start with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet does not run them.

**Quotas**

- An admin sets a person's **group** (up to 64 characters, for example `vision-lab`) and **monthly GPU-hour quota** under **Settings → Members → Limits**, or with `PATCH /api/w/{ws}/members/{user_id}/compute` `{"group": "...", "gpu_hour_quota": 40}`. A workspace default applies to members with no quota of their own: Settings → Members, or `PATCH /api/w/{ws}/compute-settings` `{"default_gpu_hour_quota": 20}`. Empty or `null` means unlimited. A quota of `0` closes the workspace's own nodes to that person.
- Owners and admins are not exempt: they are limited by their own quota, or by the default if they have none. There is no way to exempt one member from a default; leave the default empty and set quotas individually.
- When a member's own-node usage this month has reached the quota, a new job is refused with `409 QUOTA_EXCEEDED`, saying what was used and when the quota resets (the first of next month, UTC). A job pinned to a rental or a listing (`rental_id`, `offer_id`) is not checked.
- A job that was already queued when its submitter went over quota stays queued with "waiting: monthly GPU-hour quota used" and does not take a GPU on the workspace's own nodes. It can still be placed on rented GPUs, and it starts when the quota is raised or the month rolls over.
- **Running jobs are never stopped.** A long job can take a person past their quota; the quota stops the next job, not this one.
- Quotas are checked when a job is submitted and when a queued job is considered (every background pass), so a quota can be exceeded by the jobs running at that moment.

## Roles

| Action | Viewer | Operator | Admin |
| --- | --- | --- | --- |
| See jobs, listings, billing | ✓ | ✓ | ✓ |
| Run and cancel jobs, read a service's token | | ✓ | ✓ |
| See GPU-hour usage | ✓ | ✓ | ✓ |
| Set groups and quotas, rent, list (public or private), create API keys | | | ✓ |

API keys (`mck_…`) act in one workspace with the role you choose. They cannot manage members.

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
