// Meridian Cloud API (meridian-control in SaaS mode). Same-origin, cookie
// session; every request carries X-Meridian-CSRF so cross-site pages can't
// ride the cookie.

import { request } from "@/lib/api/client"

import type { BackendStatus, CanaryStatus, RequestRecord } from "@/lib/api/types"

export type WorkspaceRole = "owner" | "admin" | "operator" | "viewer"

export interface CloudInfo {
  mode: "cloud"
  version: string
  providers: { google: boolean; email: boolean }
  email_delivery: "smtp" | "console"
  signup: "open" | "invite"
  public_url: string
  node_url: string
}

export interface CloudUser {
  id: string
  email: string
  name: string
  avatar_url: string
  created_at: string | null
}

export interface WorkspaceRef {
  id: string
  slug: string
  name: string
  role: WorkspaceRole
}

export interface Me {
  user: CloudUser
  workspaces: WorkspaceRef[]
  /** Operates this Meridian Cloud: may record credits and payouts. */
  platform_admin?: boolean
}

export interface WorkspaceInfo extends WorkspaceRef {
  created_at: string | null
  members: number
}

export interface Member extends CloudUser {
  role: WorkspaceRole
  joined_at: string | null
  last_login_at: string | null
}

export interface Invitation {
  id: string
  email: string
  role: WorkspaceRole
  created_at: string | null
  expires_at: string | null
}

export interface SessionView {
  id: string
  method: string
  user_agent: string
  ip: string
  created_at: string | null
  last_seen_at: string | null
  expires_at: string | null
  current: boolean
}

export interface GatewayTokenView {
  id: string
  name: string
  prefix: string
  created_at: string | null
  last_used_at: string | null
  revoked: boolean
}

export interface GatewayWindow {
  seconds?: number
  requests?: number
  errors?: number
  client_errors?: number
  rps?: number
  p50_ms?: number | null
  p95_ms?: number | null
}

export interface GatewaySummary {
  gateway_id: string
  name: string
  version: string
  online: boolean
  first_seen_at: string | null
  last_seen_at: string | null
  strategy: string | null
  backends_total: number
  backends_healthy: number
  window: GatewayWindow
  canary: CanaryStatus | null
  /** Where workspace members sign in to this gateway's own console. */
  console_url?: string | null
}

export interface GatewayReport {
  gateway_id: string
  name: string
  version: string
  interval_s: number
  sent_at: string
  received_at: string
  status: { strategy: string; backends: BackendStatus[]; canary?: CanaryStatus }
  window: GatewayWindow
  totals: Record<string, number>
  features: Record<string, boolean>
  recent_requests: RequestRecord[]
}

export interface GatewayDetail extends GatewaySummary {
  report: GatewayReport
}

// ── Meridian Compute: marketplace, jobs, billing. Money is integer paise. ──

export interface Offer {
  id: string
  title: string
  description: string
  region: string
  gpu_model: string
  gpu_count: number
  vram_bytes_per_gpu: number
  price_paise_per_gpu_hour: number
  fee_bps: number
  hourly_paise: number
  hourly_fee_paise: number
  hourly_total_paise: number
  status: "listed" | "paused" | "retired"
  available: boolean
  unavailable_reason: string
  provider: { name: string; slug: string }
  mine: boolean
  node_id?: string
  created_at: string | null
}

export interface ListableNode {
  node_id: string
  display_name: string
  gpu_model: string
  gpu_count: number
  vram_bytes_per_gpu: number
  listable: boolean
  reason: string
}

export interface Rental {
  id: string
  role: "consumer" | "provider"
  status: "active" | "ended"
  end_reason: string
  offer: { id: string; title: string; region: string; gpu_model: string; vram_bytes_per_gpu: number }
  counterparty: string
  gpu_count: number
  price_paise_per_gpu_hour: number
  fee_bps: number
  hourly_total_paise: number
  amount_paise: number
  fee_paise: number
  seconds: number
  node_online: boolean
  release_after_job_id: string | null
  started_at: string | null
  ended_at: string | null
}

export type JobStatus = "queued" | "scheduled" | "running" | "succeeded" | "failed" | "cancelled"

export interface JobSummary {
  id: string
  name: string
  kind: "job" | "service"
  image: string
  status: JobStatus
  message: string
  replicas: number
  gpus_per_replica: number
  min_vram_bytes: number
  timeout_seconds: number
  rental_id: string | null
  created_by: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  seconds: number
  phases: string[]
}

export interface Placement {
  rank: number
  node_id: string
  node_name: string
  gpus: number
  phase: string
  exit_code: number | null
  reason: string
  message: string
  endpoint: string
  started_at: string | null
  finished_at: string | null
  log_tail: string
}

export interface JobDetail extends JobSummary {
  args: string[]
  env: Record<string, string>
  expose_port: boolean
  placements: Placement[]
  service?: { endpoint: string; token: string | null }
}

export interface JobSpec {
  name?: string
  kind: "job" | "service"
  image: string
  args: string[]
  env: Record<string, string>
  replicas: number
  gpus_per_replica: number
  min_vram_gb: number
  timeout_seconds: number
  offer_id?: string
  rental_id?: string
  /** A job template id: its fields are the defaults, and any field above overrides them. */
  template?: string
}

/** A one-click starting point for the "Run a job" form (GET /job-templates). */
export interface JobTemplate {
  id: string
  title: string
  summary: string
  kind: "job" | "service"
  image: string
  args: string[]
  env: Record<string, string>
  gpus_per_replica: number
  replicas: number
  timeout_seconds: number
  docs: string
}

export interface LedgerEntry {
  id: number
  kind: string
  amount_paise: number
  rental_id: string | null
  reference: string
  memo: string
  created_at: string | null
}

export interface Billing {
  currency: string
  fee_bps: number
  balance_paise: number
  accrued_spend_paise: number
  accrued_earnings_paise: number
  available_paise: number
  burn_paise_per_hour: number
  totals_paise: Record<string, number>
  entries: LedgerEntry[]
}

export interface ApiKeyView {
  id: string
  name: string
  role: "viewer" | "operator" | "admin"
  prefix: string
  created_by: string
  created_at: string | null
  last_used_at: string | null
  revoked: boolean
}

export interface PlatformSummary {
  fee_bps: number
  fee_revenue_paise: number
  gross_rental_paise: number
  active_rentals: number
  listed_offers: number
  workspaces: { id: string; slug: string; name: string; balance_paise: number }[]
}

const H = { "X-Meridian-CSRF": "1" }
const o = { auth: false as const, headers: H }
const w = (ws: string) => `/api/w/${encodeURIComponent(ws)}`

export const cloud = {
  info: (signal?: AbortSignal) => request<CloudInfo>("/api/cloud/info", { ...o, signal }),
  me: (signal?: AbortSignal) => request<Me>("/api/me", { ...o, signal }),
  updateMe: (name: string) => request<{ user: CloudUser }>("/api/me", { ...o, method: "PATCH", body: { name } }),
  sessions: (signal?: AbortSignal) =>
    request<{ sessions: SessionView[] }>("/api/me/sessions", { ...o, signal }).then((r) => r.sessions),
  revokeSession: (id: string) =>
    request<{ ok: boolean }>(`/api/me/sessions/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { ...o, method: "POST" }),

  emailStart: (email: string) => request<{ sent: boolean }>("/auth/email/start", { ...o, method: "POST", body: { email } }),
  emailVerify: (email: string, code: string) =>
    request<{ user: CloudUser }>("/auth/email/verify", { ...o, method: "POST", body: { email, code } }),
  googleStartUrl: (next: string) => `/auth/google/start?next=${encodeURIComponent(next)}`,

  createWorkspace: (name: string) =>
    request<WorkspaceRef>("/api/workspaces", { ...o, method: "POST", body: { name } }),
  workspace: (ws: string, signal?: AbortSignal) => request<WorkspaceInfo>(w(ws), { ...o, signal }),
  renameWorkspace: (ws: string, name: string) =>
    request<WorkspaceInfo>(w(ws), { ...o, method: "PATCH", body: { name } }),

  members: (ws: string, signal?: AbortSignal) =>
    request<{ members: Member[]; invitations: Invitation[] }>(`${w(ws)}/members`, { ...o, signal }),
  invite: (ws: string, email: string, role: WorkspaceRole) =>
    request<Invitation>(`${w(ws)}/invitations`, { ...o, method: "POST", body: { email, role } }),
  revokeInvite: (ws: string, id: string) =>
    request<{ ok: boolean }>(`${w(ws)}/invitations/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),
  setRole: (ws: string, userId: string, role: WorkspaceRole) =>
    request<{ ok: boolean }>(`${w(ws)}/members/${encodeURIComponent(userId)}`, { ...o, method: "PATCH", body: { role } }),
  removeMember: (ws: string, userId: string) =>
    request<{ ok: boolean }>(`${w(ws)}/members/${encodeURIComponent(userId)}`, { ...o, method: "DELETE" }),

  gatewayTokens: (ws: string, signal?: AbortSignal) =>
    request<{ tokens: GatewayTokenView[] }>(`${w(ws)}/gateway-tokens`, { ...o, signal }).then((r) => r.tokens),
  createGatewayToken: (ws: string, name: string) =>
    request<GatewayTokenView & { token: string }>(`${w(ws)}/gateway-tokens`, { ...o, method: "POST", body: { name } }),
  revokeGatewayToken: (ws: string, id: string) =>
    request<{ ok: boolean }>(`${w(ws)}/gateway-tokens/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),

  gateways: (ws: string, signal?: AbortSignal) =>
    request<{ gateways: GatewaySummary[] }>(`${w(ws)}/gateways`, { ...o, signal }).then((r) => r.gateways),
  gateway: (ws: string, id: string, signal?: AbortSignal) =>
    request<GatewayDetail>(`${w(ws)}/gateways/${encodeURIComponent(id)}`, { ...o, signal }),
  forgetGateway: (ws: string, id: string) =>
    request<{ ok: boolean }>(`${w(ws)}/gateways/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),

  offers: (ws: string, mine: boolean, signal?: AbortSignal) =>
    request<{ offers: Offer[] }>(`${w(ws)}/market/offers${mine ? "?mine=true" : ""}`, { ...o, signal }).then((r) => r.offers),
  listableNodes: (ws: string, signal?: AbortSignal) =>
    request<{ nodes: ListableNode[] }>(`${w(ws)}/market/listable-nodes`, { ...o, signal }).then((r) => r.nodes),
  createOffer: (ws: string, body: { node_id: string; title: string; region: string; description: string; price_paise_per_gpu_hour: number }) =>
    request<Offer>(`${w(ws)}/market/offers`, { ...o, method: "POST", body }),
  updateOffer: (ws: string, id: string, body: Partial<{ status: "listed" | "paused"; price_paise_per_gpu_hour: number }>) =>
    request<Offer>(`${w(ws)}/market/offers/${encodeURIComponent(id)}`, { ...o, method: "PATCH", body }),
  retireOffer: (ws: string, id: string) =>
    request<void>(`${w(ws)}/market/offers/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),
  rentals: (ws: string, signal?: AbortSignal) =>
    request<{ rentals: Rental[] }>(`${w(ws)}/market/rentals`, { ...o, signal }).then((r) => r.rentals),
  rent: (ws: string, offerId: string) =>
    request<Rental>(`${w(ws)}/market/rentals`, { ...o, method: "POST", body: { offer_id: offerId } }),
  release: (ws: string, id: string) =>
    request<Rental>(`${w(ws)}/market/rentals/${encodeURIComponent(id)}/release`, { ...o, method: "POST" }),

  jobs: (ws: string, signal?: AbortSignal) =>
    request<{ jobs: JobSummary[] }>(`${w(ws)}/jobs`, { ...o, signal }).then((r) => r.jobs),
  job: (ws: string, id: string, signal?: AbortSignal) =>
    request<JobDetail>(`${w(ws)}/jobs/${encodeURIComponent(id)}`, { ...o, signal }),
  jobTemplates: (ws: string, signal?: AbortSignal) =>
    request<{ templates: JobTemplate[] }>(`${w(ws)}/job-templates`, { ...o, signal }).then((r) => r.templates),
  submitJob: (ws: string, body: JobSpec) => request<JobDetail>(`${w(ws)}/jobs`, { ...o, method: "POST", body }),
  cancelJob: (ws: string, id: string) =>
    request<JobDetail>(`${w(ws)}/jobs/${encodeURIComponent(id)}/cancel`, { ...o, method: "POST" }),

  billing: (ws: string, signal?: AbortSignal) => request<Billing>(`${w(ws)}/billing`, { ...o, signal }),
  apiKeys: (ws: string, signal?: AbortSignal) =>
    request<{ keys: ApiKeyView[] }>(`${w(ws)}/api-keys`, { ...o, signal }).then((r) => r.keys),
  createApiKey: (ws: string, name: string, role: ApiKeyView["role"]) =>
    request<ApiKeyView & { token: string }>(`${w(ws)}/api-keys`, { ...o, method: "POST", body: { name, role } }),
  revokeApiKey: (ws: string, id: string) =>
    request<void>(`${w(ws)}/api-keys/${encodeURIComponent(id)}`, { ...o, method: "DELETE" }),

  platformSummary: (signal?: AbortSignal) => request<PlatformSummary>("/api/platform/summary", { ...o, signal }),
  platformCredit: (body: { workspace: string; amount_paise: number; reference: string; memo: string }) =>
    request<{ workspace_id: string; balance_paise: number }>("/api/platform/credits", { ...o, method: "POST", body }),
  platformPayout: (body: { workspace: string; amount_paise: number; reference: string; memo: string }) =>
    request<{ workspace_id: string; balance_paise: number }>("/api/platform/payouts", { ...o, method: "POST", body }),
}

/** "₹ per hour" typed by a person → integer paise, or null if it is not money. */
export function rupeesToPaise(text: string): number | null {
  const m = /^\s*(\d{1,9})(?:\.(\d{1,2}))?\s*$/.exec(text)
  if (!m) return null
  return Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"))
}

const RANK: Record<WorkspaceRole, number> = { viewer: 0, operator: 1, admin: 2, owner: 3 }

export function atLeast(role: WorkspaceRole | undefined, minimum: WorkspaceRole): boolean {
  return role !== undefined && RANK[role] >= RANK[minimum]
}

/**
 * Boot info embedded by meridian-control in the page it serves
 * (`<script id="meridian-boot" type="application/json">`).
 *
 * Returns the info in cloud mode, `null` if the block is present but not
 * cloud, and `undefined` when there is no block (a self-hosted gateway
 * served this page) — so production boot needs no network round trip.
 */
export function readBootInfo(doc: Document = document): CloudInfo | null | undefined {
  const el = doc.getElementById("meridian-boot")
  if (!el) return undefined
  try {
    const body = JSON.parse(el.textContent ?? "") as Partial<CloudInfo> | null
    return body?.mode === "cloud" ? (body as CloudInfo) : null
  } catch {
    return null
  }
}

/**
 * Decide at boot whether this console is served by Meridian Cloud (dev
 * server only: production pages carry the answer, see readBootInfo).
 *
 * A self-hosted gateway answers /api/cloud/info with a fast 404, so that
 * means "gateway mode". Network errors and 5xx (control plane restarting
 * behind its proxy) are retried briefly before falling back.
 */
export async function detectCloud(fetchImpl: typeof fetch = fetch, delays: number[] = [250, 750]): Promise<CloudInfo | null> {
  for (let attempt = 0; ; attempt++) {
    try {
      const r = await fetchImpl("/api/cloud/info", { credentials: "same-origin", headers: { Accept: "application/json" } })
      if (r.ok) {
        if (!(r.headers.get("content-type") ?? "").includes("json")) return null
        const body = (await r.json()) as Partial<CloudInfo> | null
        return body?.mode === "cloud" ? (body as CloudInfo) : null
      }
      if (r.status < 500) return null
    } catch {
      /* network error: retry */
    }
    if (attempt >= delays.length) return null
    await new Promise((res) => setTimeout(res, delays[attempt]))
  }
}
