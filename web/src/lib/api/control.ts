import { request } from "./client"
import type {
  AuditEvent,
  Claim,
  ControlOverview,
  EnrollmentTokenView,
  NodeDetail,
  NodeSummary,
  PlacementResult,
  ProjectionEndpoint,
} from "./types"

/**
 * meridian-control admin API. A separate service and trust domain from the
 * gateway: the Meridian API key is never attached (`auth: false`) and calls go
 * to the operator-configured base URL (direct + CORS, or a same-origin path
 * behind the operator's reverse proxy).
 */
export function controlApi(baseUrl: string) {
  const opts = { baseUrl, auth: false as const }
  const nodePath = (id: string) => `/admin/nodes/${encodeURIComponent(id)}`
  return {
    health: (signal?: AbortSignal) => request<{ status: string }>("/healthz", { ...opts, signal }),
    overview: (signal?: AbortSignal) => request<ControlOverview>("/admin/overview", { ...opts, signal }),
    nodes: (signal?: AbortSignal) =>
      request<{ nodes: NodeSummary[] }>("/admin/nodes", { ...opts, signal }).then((r) => r.nodes),
    node: (id: string, signal?: AbortSignal) => request<NodeDetail>(nodePath(id), { ...opts, signal }),
    claims: (status?: string, signal?: AbortSignal) =>
      request<{ claims: Claim[] }>("/admin/claims", { ...opts, query: { status }, signal }).then((r) => r.claims),
    tokens: (signal?: AbortSignal) =>
      request<{ tokens: EnrollmentTokenView[] }>("/admin/tokens", { ...opts, signal }).then((r) => r.tokens),
    audit: (q: { limit?: number; node_id?: string } = {}, signal?: AbortSignal) =>
      request<{ events: AuditEvent[] }>("/admin/audit", { ...opts, query: q, signal }).then((r) => r.events),
    projection: (signal?: AbortSignal) =>
      request<{ endpoints: ProjectionEndpoint[] }>("/admin/projection", { ...opts, signal }).then((r) => r.endpoints),
    crl: (signal?: AbortSignal) =>
      request<{ revoked: { node_id: string; serial: string | null }[] }>("/admin/crl", { ...opts, signal }).then(
        (r) => r.revoked,
      ),

    mintToken: (body: { auto_approve: boolean; ttl_seconds: number }) =>
      request<{ token: string }>("/admin/tokens", { ...opts, method: "POST", body }),
    approveClaim: (id: string) =>
      request<{ ok: boolean }>(`/admin/claims/${encodeURIComponent(id)}/approve`, { ...opts, method: "POST" }),
    denyClaim: (id: string) =>
      request<{ ok: boolean }>(`/admin/claims/${encodeURIComponent(id)}/deny`, { ...opts, method: "POST" }),
    setDesired: (id: string, snapshot: Record<string, unknown>) =>
      request<{ ok: boolean }>(`${nodePath(id)}/desired`, { ...opts, method: "POST", body: snapshot }),
    authorizeStop: (id: string, engineIds: string[]) =>
      request<{ ok: boolean }>(`${nodePath(id)}/stop-authorize`, {
        ...opts,
        method: "POST",
        body: { engine_ids: engineIds },
      }),
    revoke: (id: string) => request<{ ok: boolean }>(`${nodePath(id)}/revoke`, { ...opts, method: "POST" }),
    restore: (highWaterEpoch: number) =>
      request<{ incarnation: number }>("/admin/restore", {
        ...opts,
        method: "POST",
        body: { high_water_epoch: highWaterEpoch },
      }),
    place: (body: { required_vram_bytes: number; count: number; artifact_digest?: string }) =>
      request<{ placement: PlacementResult | null }>("/admin/place", { ...opts, method: "POST", body }).then(
        (r) => r.placement,
      ),
  }
}

export type ControlApi = ReturnType<typeof controlApi>
