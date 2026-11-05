# Meridian Cloud: host the control plane for free

Meridian Cloud is `meridian-control` running in **cloud mode**. Operators sign in
with Google or an emailed code, work in **workspaces** with roles, and connect
**gateways** and **GPU nodes** that run in their own networks.

This guide runs it for **$0** on a machine you already own: an Arduino UNO Q,
a Raspberry Pi, a Linux laptop or an Oracle Cloud Always Free VM. It is
published on the internet with **Tailscale Funnel**, so you need no port
forwarding, no static IP and no domain purchase, and it works behind CGNAT and
mobile data.

```
          browsers (Google / email sign-in)           gateways (token)            GPU nodes (mTLS)
                    │ https://<host>.ts.net                │ https://<host>.ts.net    │ https://<host>.ts.net:8443
                    ▼                                      ▼                          ▼
 ┌──────────────── Tailscale Funnel (raw TCP, TLS untouched, PROXY v2 → real client IP) ────────────────┐
 │                                                                                                         │
 │  your machine   127.0.0.1:8441  Caddy  TLS (*.ts.net cert from tailscaled)      ──┐                    │
 │                 127.0.0.1:8442  Caddy  TLS + client certs (Meridian node CA)    ──┤                    │
 │                                                                                    ▼                    │
 │                 127.0.0.1:8090  meridian-control (cloud mode) · SQLite · node CA                       │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Hybrid by design: prompts never reach Meridian Cloud.** Gateways keep
serving traffic inside the customer's network. Each one connects outbound to:

- **pull** the engines its workspace may route to (`/api/gateway/v1/projection`)
- **report** health every 15 s (`/api/gateway/v1/report`). Reports carry
  metadata only: backend state, latency percentiles, status counts and
  request IDs. No prompts, completions or API keys are sent.

If the cloud is unreachable, gateways keep routing with the last-known
engines, and nodes keep their engines running.

## What it costs

| Piece | Free option | Limit worth knowing |
| --- | --- | --- |
| Compute | UNO Q / Pi / laptop you own, or [Oracle Cloud Always Free](https://www.oracle.com/cloud/free/) Ampere A1 (up to 4 OCPU, 24 GB) | A laptop that sleeps takes the console offline (gateways keep serving) |
| Public HTTPS + name | [Tailscale](https://tailscale.com/pricing) Personal plan + Funnel → `https://<machine>.<tailnet>.ts.net` | Funnel ports are 443, 8443, 10000; bandwidth is fine for control traffic |
| TLS certificates | Let's Encrypt via `tailscaled` (automatic) | MagicDNS + HTTPS certificates must be on |
| Google sign-in | Google Cloud OAuth client | Basic scopes (`openid email profile`) need no app verification |
| Email codes | Gmail SMTP with an App Password, or [Brevo](https://www.brevo.com/pricing/) free | Gmail ≈ 500 messages/day; Brevo 300/day |
| Database | SQLite on the machine (online backups via `meridian-control backup`) | One writer; ample for dozens of gateways and hundreds of nodes |
| Images | `ghcr.io/imv-in/meridian-control` (amd64 + arm64) | — |

Phone-number (SMS) sign-in is deliberately not included: SMS has no free
tier. Google and email codes cover sign-in.

## 1. Prepare the machine

Any 64-bit Linux with Docker works. Run `docker compose version` to check.

- **Arduino UNO Q (4 GB).** Debian on arm64. Docker ships with the board's
  App Lab image; if `docker compose` is missing,
  `sudo apt install docker-compose-plugin`. Use wired power and a stable
  network, because the board is now a server.
- **Linux laptop.** Turn off suspend on lid close while it hosts, e.g.
  `HandleLidSwitch=ignore` in `/etc/systemd/logind.conf`.
- **Windows / macOS.** Run the stack inside WSL2 (Ubuntu) or a small Linux VM
  with Tailscale installed *inside* it. Alternatively, use the Oracle free VM.

Install Tailscale and join your tailnet:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

```bash
sudo tailscale up
```

In the [Tailscale admin console](https://login.tailscale.com/admin/dns),
turn on **MagicDNS** and **HTTPS Certificates**. The first `tailscale funnel`
command prints a link to enable Funnel for this machine if your policy does
not already allow it. Note your machine's name:

```bash
tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
```

## 2. Configure

```bash
git clone https://github.com/IMV-IN/Meridian.git && cd Meridian/deploy/cloud
```

```bash
cp .env.example .env && chmod 600 .env
```

Edit `.env`:

- `MERIDIAN_HOST`: the `*.ts.net` name from step 1.
- `MERIDIAN_CONTROL_SIGNUP=invite` and `MERIDIAN_CONTROL_ADMIN_EMAILS=<your email>`.
  Only you can create an account; everyone else joins by invitation. Use
  `open` only for a public demo.
- Google and SMTP settings: steps 3 and 4.

Secrets live **only** in `.env` on the machine. Never commit it, paste it in
chat or put it in the gateway's `config.yaml`.

### 3. Sign in with Google (optional)

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project.
2. Go to **Google Auth Platform → Branding** and fill in the app name and
   support email. Under **Audience**, choose *External*, then **Publish app**.
   In *Testing* mode only listed test users can sign in. The basic scopes
   need no verification.
3. Go to **Clients → Create client → Web application**. Add the authorized
   redirect URI `https://<MERIDIAN_HOST>/auth/google/callback`.
4. Put the client ID and secret in `.env` as
   `MERIDIAN_CONTROL_GOOGLE_CLIENT_ID` / `MERIDIAN_CONTROL_GOOGLE_CLIENT_SECRET`.

Meridian uses the authorization-code flow with PKCE, `state` and `nonce`.
It reads the ID token straight from Google's token endpoint over TLS, and
signs in only Google-verified email addresses.

### 4. Email sign-in codes

**Gmail.** Turn on 2-Step Verification for the Google account. Create an
**App Password** (Google Account → Security → App passwords), then set:

```
MERIDIAN_CONTROL_SMTP_HOST=smtp.gmail.com
MERIDIAN_CONTROL_SMTP_PORT=587
MERIDIAN_CONTROL_SMTP_SECURITY=starttls
MERIDIAN_CONTROL_SMTP_USERNAME=you@gmail.com
MERIDIAN_CONTROL_SMTP_PASSWORD=<16-character app password>
MERIDIAN_CONTROL_MAIL_FROM=Meridian <you@gmail.com>
```

**Brevo.** Verify a sender address and use host `smtp-relay.brevo.com`,
port 587, `starttls`, with your SMTP login and key.

Codes are 6 digits and valid for 10 minutes. A code allows 5 attempts and is
stored only as an HMAC. Requests are limited per address (5 per hour, 30 s
apart) and per network. Responses never reveal whether an account exists.
Without SMTP, codes are printed in the `control` container log, and the
sign-in page warns that it is in development mode.

## 5. Start and publish

```bash
sudo install -d -o 10002 -g 10002 data      # database, node CA, session key
```

```bash
docker compose run --rm migrate             # creates the DB and data/ca/ca_cert.pem
```

```bash
docker compose up -d
```

```bash
./funnel.sh on
```

`docker compose` pulls `ghcr.io/imv-in/meridian-control` for your platform.
If that tag is not published yet, it builds from this checkout instead
(several minutes on a UNO Q). To skip building on the board, build on a laptop
and copy the image over:

```bash
docker buildx build --platform linux/arm64 -f deploy/control/Dockerfile -t ghcr.io/imv-in/meridian-control:latest --load .
```

```bash
docker save ghcr.io/imv-in/meridian-control:latest | ssh <board> docker load
```

Check it:

```bash
curl -fsS https://$MERIDIAN_HOST/healthz
```

Open `https://<MERIDIAN_HOST>/ui`, sign in with your admin email, and create
your first workspace.

## 6. Use it

- **Invite your team.** In **Settings → Members**, invite people by email as
  viewer, operator or admin. They join as soon as they sign in with that
  address, by Google or email code.
- **Connect a gateway.** On **Gateways → Connect gateway**, mint a token (shown
  once). On the gateway host:

  ```yaml
  # config.yaml
  control_plane:
    enabled: true
    url: https://<MERIDIAN_HOST>
    gateway_name: prod-mumbai-1
  ```

  ```bash
  export MERIDIAN_CLOUD_TOKEN='mgw_…'   # never in config.yaml
  ```

  The gateway appears within one report interval. Its identity defaults to
  `hostname:port`; set `control_plane.gateway_id` if hostnames change, for
  example with Kubernetes pods.
- **Enroll a GPU node.** On **Enrollment → Mint token**, then on the GPU host:

  ```bash
  export MERIDIAN_NODE_TOKEN='<join token>'
  ```

  Set `control_plane_url: https://<MERIDIAN_HOST>:8443` in
  `/etc/meridian-node/config.yaml`, then run `meridian-node run`. The node
  enrolls, proves possession of its key and receives a certificate from your
  node CA. Every later call is mutually authenticated. The node verifies the
  server with its normal trust store, because `*.ts.net` certificates are
  publicly trusted.

## How the edge is secured

| Path | Exposed on | Authentication |
| --- | --- | --- |
| `/ui`, `/api/*`, `/auth/*` | 443 | Session cookie (`__Host-`, HttpOnly, Secure, SameSite=Lax). Writes also need the `X-Meridian-CSRF` header |
| `/api/gateway/v1/*` | 443 | `Authorization: Bearer mgw_…` (stored hashed, revocable per token) |
| `/control/v1/enroll` | 8443 | One-time join token |
| `/control/v1/nodes/*` | 8443 | Client certificate. Caddy verifies it against the node CA and forwards it; meridian-control re-verifies the chain and the node ID (`REQUIRE_MTLS=1`) |
| `/control/v1/*` | 443 | Refused (421). Nodes must use 8443 |

Caddy removes any client-supplied `X-Client-Cert` on 443 and overwrites it on
8443, so a forged certificate header cannot reach meridian-control. Both
listeners bind to 127.0.0.1; only Funnel reaches them. The console is served
with a strict CSP (`script-src 'self'`, `frame-ancestors 'none'`), `no-store`
on API responses, and HSTS.

**Checklist**

- [ ] `.env` is `chmod 600` and not in git.
- [ ] `SIGNUP=invite` (or `ALLOWED_EMAIL_DOMAINS` set) unless the instance is a public demo.
- [ ] Real SMTP is configured, so the sign-in page shows no development-mode notice.
- [ ] Backups run daily and a restore has been tested (below).
- [ ] `data/ca/ca_key.pem` is readable only by uid 10002, and backups are private.
- [ ] Gateway tokens are scoped per environment and revoked when a host is retired.

## Back up and restore

```bash
sudo ./backup.sh        # writes backups/meridian-<timestamp>.tar.gz, keeps 14
```

Each archive holds a consistent SQLite snapshot, taken with the online backup
API, plus the node CA and session key. Copy archives off the machine to a
private location you trust.

To restore onto a fresh machine:

```bash
docker compose down
```

```bash
sudo tar -xzf backups/meridian-<timestamp>.tar.gz -C data && sudo mv data/backup-*.db data/control.db && sudo chown -R 10002:10002 data
```

```bash
docker compose run --rm control restore --high-water-epoch <last epoch you saw>
```

```bash
docker compose up -d
```

`restore` raises the fencing-epoch floor, so a node session from before the
backup can never act again. See the restore runbook in
[`OPS_RUNBOOK.md`](OPS_RUNBOOK.md).

## Update

```bash
git pull && docker compose pull && docker compose run --rm migrate && docker compose up -d
```

## Limits and when to move

- **Availability is your machine's.** For always-on hosting at no cost, move
  the same `deploy/cloud` kit to an Oracle Cloud Always Free Ampere VM
  (arm64, same image). Restore a backup there and run `./funnel.sh on` on the
  new machine.
- **Custom domain.** Funnel serves `*.ts.net` names only. For the console on
  your own domain, you could put a Cloudflare Tunnel (free) in front of the
  443 listener. Cloudflare terminates TLS, though, so keep GPU nodes on the
  Funnel `:8443` address, where client certificates pass through untouched.
- **Scale.** SQLite with one writer suits a small team's fleet. Point
  `MERIDIAN_CONTROL_DB_URL` at Postgres when you outgrow it; migrations are
  the same.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `tailscale funnel` says Funnel is not enabled | Open the link it prints, or add the `funnel` attribute for this node in the tailnet policy |
| Browser certificate error | HTTPS Certificates is off in the Tailscale admin, or Caddy cannot reach `/var/run/tailscale/tailscaled.sock`. Check with `docker compose logs caddy` |
| Caddy fails with `node-ca.pem` is a directory | Docker created the mount before the CA existed. `docker compose down`, `sudo rm -r data/ca/ca_cert.pem`, `docker compose run --rm migrate`, `up -d` |
| Email codes never arrive | Check `docker compose logs control` for SMTP errors. Gmail needs an App Password, not your account password |
| "That sign-in link expired" after Google | The sign-in took over 10 minutes, or cookies are blocked. Start again from `/ui` |
| Node calls get `421 use the node endpoint` | Set the node's `control_plane_url` to `https://<host>:8443` |
| Node gets `401 client certificate required` | The node is calling 8443 without its certificate. Re-enroll the node with a new join token |
| Gateway shows "Offline" | It has not reported for 3 intervals. Check that it can reach `https://<host>`, and check the token in Settings → Gateway tokens |

## Develop locally

```bash
python scripts/console_demo.py --cloud
```

```bash
cd web && MERIDIAN_CLOUD_URL=http://127.0.0.1:8443 npm run dev
```

The first command runs the whole system on localhost: cloud-mode control
plane, a signed-in demo workspace, simulated GPU nodes and two reporting
gateways. Sign-in codes print to the terminal. The second gives you hot
reload at http://127.0.0.1:5173/ui.
