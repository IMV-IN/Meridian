# Operator console

The Meridian console is the web UI for operators, finance and tenants. The
gateway serves it at **`/ui`** from the same origin as the API, so it needs no
extra service, no CDN and no outbound network access. That keeps it usable in
air-gapped installs.

It covers every gateway and control-plane workflow: live health and traffic,
routing and rollout state, the request log, a chat playground, API key
lifecycle, usage and cost, budgets, rate limits, PII policy, and the GPU fleet
managed by `meridian-control`.

> The console reads and acts through the same APIs you can call yourself
> (`/meridian/*`, `/v1/*`, `/metrics`, and `meridian-control`'s `/admin/*`).
> It adds no privileges of its own. Every permission is enforced server-side.

> **Meridian Cloud.** The same console also runs in *cloud mode*, served by
> `meridian-control` with accounts (Google / email codes), workspaces and
> connected gateways instead of API-key sign-in. It switches automatically
> when `/api/cloud/info` answers. See [`CLOUD.md`](CLOUD.md).

## Open it

| How you run Meridian | Console |
|---|---|
| `docker compose up --build` or the published image | `http://<gateway>:8080/ui`. The image builds the console. |
| From source with `pip install -e .` | `cd web && npm ci && npm run build` once, then `/ui`. Without a build, `/ui` serves the single-page legacy dashboard. |

The Meridian Cloud control plane also serves the documentation at `/docs/`, built
separately with `mkdocs build --strict -d meridian_control/docs_site` (see [Meridian Cloud](CLOUD.md#documentation-at-docs)).

With `auth.enabled: true` the console asks for an API key. The key is validated
with `GET /meridian/whoami` and then sent only to this gateway as a Bearer
token. By default it is kept for the browser tab only (sessionStorage). Choose
**Remember on this device** to keep it in localStorage. If the key is deleted or
rotated, the console signs you out with an explanation.

When the gateway is connected to a Meridian Cloud workspace, the sign-in page
offers **Sign in with Meridian Cloud**: workspace members sign in with their
cloud account and their workspace role sets their rights here (viewer,
operator, or admin for admins and owners). API keys remain available as
break-glass unless `control_plane.console_key_sign_in` is false. See
[Gateway console sign-in](CLOUD.md#gateway-console-sign-in).

With auth disabled (and no Meridian Cloud connection) the console runs in
**open mode** and shows a banner. Key
management, config reload and cost export all need auth, so they are
unavailable in open mode.

## What each role sees

The console asks `/meridian/whoami` what the key may do and shows or hides
actions to match. Each right is still enforced again on every API call.

| Right | Tenant | Viewer | Operator | Admin | `cost_admin` | `ops_admin` |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Operator views (overview, requests, backends, routing, observability, policies, fleet) | | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reload configuration | | | ✓ | ✓ | | ✓ |
| Create / delete API keys | | | | ✓ | | ✓ |
| Usage & cost for every org | own org | own org | own org | ✓ | ✓ | own org |
| Playground (`/v1/*`, within allowed models) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Tenant keys without operator rights open on their own **Usage & cost** page.

## Pages

| Page | What it answers | Data |
|---|---|---|
| **Overview** | Is the fleet healthy right now? Throughput, success rate, p95, backends in rotation, live traffic by response class, latency percentiles, backend load, canary/tier state, fleet summary, recent errors | `/meridian/status`, `/meridian/requests`, `/metrics` |
| **Requests** | What just happened? The last 100 requests with filters, a detail drawer, error explanations and CSV/JSON export. Freeze stops the live tail while you look. | `/meridian/requests` |
| **Observability** | Lifetime counters: per-backend SLOs (p50/p90/p99), latency histogram, tokens by model, rate-limit/budget/PII rejections, cache-affinity outcomes, plus a metric explorer and a scrape config | `/metrics` |
| **Backends** | Each engine's health, circuit, EWMA, in-flight load and telemetry. The detail page adds throughput, latency distribution, status mix, pool membership and effective timeouts. | `/meridian/status`, `/meridian/config`, `/metrics` |
| **Routing & rollout** | How a request is routed: isolation, canary split, tiering, session affinity, strategy. Shows the live canary schedule and rollback headroom, tier pools and the backends that match them, and isolation pools. | `/meridian/config`, `/meridian/status` |
| **Playground** | Does the gateway route my request the way I expect? Streams a chat completion and shows the backend, tier, session pin, budget headroom, TTFT and token usage, plus cURL/Python/Node snippets | `/v1/models`, `/v1/chat/completions` |
| **Nodes** | GPU hosts with lease, engine phases, generation drift, allocatable VRAM and queue depth | control `/admin/nodes`, `/admin/overview` |
| **Node detail** | Engines, per-device capacity, NVLink groups, desired state, certificate and audit trail. Actions: **publish desired state**, **authorize stop**, **revoke**. | control `/admin/nodes/{id}` and admin actions |
| **Enrollment** | Mint one-time join tokens, approve or deny pending claims, see token and claim history | control `/admin/tokens`, `/admin/claims` |
| **Serving & placement** | Which managed engines are routable, whether this gateway registered them, the placement simulator, the CRL and the restore runbook | control `/admin/projection`, `/admin/place`, `/admin/crl`, `/admin/restore` |
| **Fleet audit** | Control-plane events: enrollments, sessions, rotations, revocations, restores | control `/admin/audit` |
| **API keys** | Create keys (the secret is shown once), delete with typed confirmation, filter by org and role, see the rights matrix | `/meridian/keys` |
| **Usage & cost** | Cost and tokens over 7, 30 or 90 days by org, team, key and model. Includes daily charts and an authenticated CSV export. | `/meridian/usage`, `/meridian/usage.csv` |
| **Policies** | Budget consumption against every cap with reset times, token-bucket rate limits and the active PII policy | `/meridian/budgets`, `/meridian/config`, `/metrics` |
| **Settings** | Theme, refresh interval, time zone, alert toasts, session details, control-plane connection, and **hot reload** | `/meridian/reload` |

Press <kbd>⌘K</kbd> (or <kbd>Ctrl K</kbd>, or <kbd>/</kbd>) to jump to any page,
backend, node or action. You can also paste a request ID there to find it. The
header **Live** toggle pauses polling. Polling also pauses in background tabs.
The bell lists changes detected while the console is open: a backend becoming
unhealthy or recovering, circuits opening and closing, canary steps and
rollbacks.

Live charts are built in the browser. Each refresh samples the cumulative
Prometheus counters and derives rates and windowed latency quantiles from the
differences, using `histogram_quantile` semantics. They show only what happened
while the console was open. Use Prometheus and Grafana
(`deploy/grafana/*.json`) for history.

## Connect the control plane

Fleet pages talk to `meridian-control` directly from the browser. In
**Settings → Control plane**, enter one of:

- **a full URL**, for example `https://control.internal:8443`. Allow the console's
  origin on the control plane and restart it:

  ```bash
  MERIDIAN_CONTROL_CORS_ORIGINS=https://gateway.internal:8080 meridian-control run
  ```

- **a same-origin path**, for example `/control`, that your reverse proxy forwards
  to `meridian-control`. This needs no CORS.

!!! warning "Control-plane admin endpoints are not authenticated"
    `meridian-control`'s `/admin/*` routes rely on network placement for
    access control. Expose them only on a private network or behind an
    authenticating proxy. The console never sends your Meridian API key to the
    control plane, and its role gating is a UX convenience there, not a security
    boundary.

## APIs added for the console

All are read-only and metadata-only. Responses are built from **allowlists**,
so raw keys, upstream `auth_header` values and file paths are never included.

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /meridian/whoami` | any valid key (open mode: none) | identity of the presenting key, its rights (`view_ops`, `reload`, `manage_keys`, `read_all_cost`) and enabled features |
| `GET /meridian/config` | operator views | effective routing and policy config: strategy and weights, health, timeouts, resilience, tiering, affinity (with live pinned-session count), canary, isolation, rate limits, budget scope counts, PII, pricing, control-plane sync |
| `GET /meridian/budgets` | operator views | every configured cap with current-period `consumed`, `remaining`, `utilization` and `resets_at` |
| `GET /admin/overview` (control) | network | incarnation, epoch floor, node counts by status, engines, routable endpoints, allocatable VRAM, settings |
| `GET /admin/nodes`, `/admin/nodes/{id}` (control) | network | node summaries. Detail adds the latest observation, desired snapshot, certificate validity, stop authorizations and recent audit events. |
| `GET /admin/claims?status=`, `/admin/tokens`, `/admin/audit` (control) | network | enrollment claims, tokens by hash prefix (never the token), and the audit trail |

## Public site

A cloud-mode `meridian-control` also serves a public site from the same bundle as the console
(`web/site.html`, source in `web/src/website/`): `/` for what Meridian is, `/labs` for institutes that share GPUs,
`/teaching` for courses, and `/install` for the install configurator. Nothing on it needs a sign-in.

- It reads one feed, `GET /api/public/offers` (see [`COMPUTE.md`](COMPUTE.md#public-listings-feed-for-a-website)),
  with TanStack Query, every 30 seconds. It shows each listing's **region**, never `provider_name`: providers have
  not agreed to be named. While the feed loads, is empty or fails, the page says so. The console recreations (job
  page, usage report, Compute limits) are labelled sample data.
- The template list in `web/src/website/templates.ts` is a copy of `meridian_control/templates.py`;
  `tests/control/test_site_templates.py` fails if they drift.
- Fonts come from `@fontsource` packages, so the site makes no request to any CDN. Under `prefers-reduced-motion`
  the demo animations show their final state and do not run.
- An older console build without `site.html` makes `/`, `/labs` and `/teaching` redirect to `/ui/`.

## Develop

```bash
# Terminal 1: full local stack: mock backends, meridian-control with
# simulated GPU nodes, gateway with every feature on, and generated traffic
pip install -e ".[dev,control]"
python scripts/console_demo.py
```

```bash
# Terminal 2: console with hot reload at http://127.0.0.1:5173/ui
cd web
npm ci
npm run dev
```

The demo prints admin, operator, viewer, finance and tenant keys so you can see
each role's view. To use a gateway other than `127.0.0.1:8080`, set
`MERIDIAN_GATEWAY_URL` before `npm run dev`.

| Command | Purpose |
|---|---|
| `npm run check` | typecheck, lint (oxlint) and unit tests (vitest) |
| `npm run build` | production build written to `meridian/ui/dist/` (gitignored, shipped in the wheel and the image), with `.br`/`.gz` copies of every text asset |
| `npm run audit:ui` | UI audit of every page against a running demo (see below) |

### UI quality bar

Every page is held to:

- **WCAG 2.2 AA**: one `main` landmark, a named sidebar and `nav`, an `h1` on every
  page, labels for every control, text contrast of at least 4.5:1 in both themes,
  and a visible focus ring on every Tab stop (including tab panels, charts,
  clickable rows and scrollable tables and code blocks).
- **[Vercel Web Interface Guidelines](https://vercel.com/design/guidelines)**:
  - forms: `name`, `autocomplete`, `inputmode` and no spellcheck on IDs, emails
    and codes; `autoFocus` only on desktop;
  - motion: `prefers-reduced-motion` honoured and no `transition: all`;
  - touch: `touch-action: manipulation` and contained overscroll in overlays;
  - theming: `theme-color` follows the theme;
  - formats: numbers and dates use the viewer's locale;
  - URLs: filters and tabs live in the query string, so views can be linked.
- **Reflow**: no horizontal page scroll at 390 px. Wide tables and tab bars
  scroll inside their own container.
- **Containment**: nothing spills out of its card, dialog, menu or control, for
  every value a select can take (a long option must not widen its dialog), and
  no text or placeholder is cut off without an ellipsis.
- **Performance**:
  - assets are served precompressed (Brotli or gzip, about 70% smaller);
  - meridian-control embeds the console's boot info in the page, so the first
    render needs no extra request;
  - layout shift stays under 0.1.

`npm run audit:ui` checks the first four automatically, running axe-core plus
reflow, containment, console-error and focus checks across light, dark and
mobile views. It also opens every dialog, select, menu, popover and the command
palette a page offers (choosing each select option in turn) and checks each one
the same way, saving a screenshot of every state for a visual pass. It exits
non-zero on any finding. `--only <regex>` limits it to matching routes:

```bash
python scripts/console_demo.py            # gateway console on :8080
cd web && npm run audit:ui -- --mode gateway
```

```bash
python scripts/console_demo.py --cloud    # Meridian Cloud on :8443; note the printed state dir
cd web && npm run audit:ui -- --mode cloud --outbox <state-dir>/outbox
```

Screenshots of every page land in `web/audit-ui-shots/` for a visual review.

**Stack:** React 19, TypeScript, Vite, Tailwind CSS v4 and
[shadcn/ui](https://ui.shadcn.com) on Radix primitives, with Recharts for charts,
TanStack Query and Table for data, cmdk for the command palette, sonner for
toasts, react-hook-form and zod for forms, and lucide icons. IBM Plex fonts are
self-hosted. The chart palette is validated for color-vision deficiency in both
themes. Status colors are reserved for state and always paired with an icon and
a label.
