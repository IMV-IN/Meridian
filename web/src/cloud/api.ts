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
