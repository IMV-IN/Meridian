import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { ApiError } from "@/lib/api/client"
import { controlApi, type ControlApi } from "@/lib/api/control"
import { gateway } from "@/lib/api/gateway"
import type { UsageQuery } from "@/lib/api/types"
import { parsePrometheus } from "@/lib/prometheus"
import { useAuth } from "@/providers/auth-provider"
import { useSettings } from "@/providers/settings-provider"

export const qk = {
  status: ["gateway", "status"] as const,
  requests: ["gateway", "requests"] as const,
  config: ["gateway", "config"] as const,
  budgets: ["gateway", "budgets"] as const,
  metrics: ["gateway", "metrics"] as const,
  models: ["gateway", "models"] as const,
  version: ["gateway", "version"] as const,
  keys: ["gateway", "keys"] as const,
  usage: (q: UsageQuery) => ["gateway", "usage", q] as const,
  control: (base: string) => ["control", base] as const,
}

// 401/403 are answers, not transient failures — don't hammer them.
const retry = (count: number, err: unknown) =>
  !(err instanceof ApiError && (err.isUnauthorized || err.isForbidden || err.isNotFound)) && count < 2

export function useStatus() {
  const { interval } = useSettings()
  const { can } = useAuth()
  return useQuery({
    queryKey: qk.status,
    queryFn: ({ signal }) => gateway.status(signal),
    refetchInterval: interval,
    enabled: can("view_ops"),
    retry,
  })
}

export function useRecentRequests() {
  const { interval } = useSettings()
  const { can } = useAuth()
  return useQuery({
    queryKey: qk.requests,
    queryFn: ({ signal }) => gateway.requests(signal),
    refetchInterval: interval,
    enabled: can("view_ops"),
    retry,
  })
}

export function useConfig() {
  const { can } = useAuth()
  const { interval } = useSettings()
  return useQuery({
    queryKey: qk.config,
    queryFn: ({ signal }) => gateway.config(signal),
    enabled: can("view_ops"),
    // Config rarely changes (reload) but active_sessions does; poll slowly.
    refetchInterval: interval ? Math.max(interval * 5, 10_000) : false,
    retry,
  })
}

export function useBudgets() {
  const { interval } = useSettings()
  const { can } = useAuth()
  return useQuery({
    queryKey: qk.budgets,
    queryFn: ({ signal }) => gateway.budgets(signal),
    enabled: can("view_ops"),
    refetchInterval: interval ? Math.max(interval * 2, 5_000) : false,
    retry,
  })
}

export function useMetricsText(enabled = true) {
  const { interval } = useSettings()
  return useQuery({
    queryKey: qk.metrics,
    queryFn: ({ signal }) => gateway.metrics(signal),
    refetchInterval: interval,
    enabled,
    retry,
  })
}

export function useMetrics(enabled = true) {
  const q = useMetricsText(enabled)
  const parsed = useMemo(() => (q.data ? parsePrometheus(q.data) : null), [q.data])
  return { ...q, parsed }
}

export function useModels() {
  return useQuery({
    queryKey: qk.models,
    queryFn: ({ signal }) => gateway.models(signal),
    staleTime: 30_000,
    retry,
  })
}

export function useVersion() {
  return useQuery({
    queryKey: qk.version,
    queryFn: ({ signal }) => gateway.version(signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry,
  })
}

export function useKeys() {
  const { can } = useAuth()
  return useQuery({
    queryKey: qk.keys,
    queryFn: ({ signal }) => gateway.listKeys(signal),
    enabled: can("manage_keys"),
    retry,
  })
}

export function useUsage(q: UsageQuery, enabled = true) {
  return useQuery({
    queryKey: qk.usage(q),
    queryFn: ({ signal }) => gateway.usage(q, signal),
    placeholderData: keepPreviousData,
    enabled,
    retry,
  })
}

// ── Control plane ─────────────────────────────────────────────────────────

/** The control-plane client, or null when no URL is configured. */
export function useControl(): { api: ControlApi | null; baseUrl: string } {
  const { settings } = useSettings()
  const baseUrl = settings.controlPlaneUrl.trim()
  const api = useMemo(() => (baseUrl ? controlApi(baseUrl) : null), [baseUrl])
  return { api, baseUrl }
}

function useControlQuery<T>(
  key: readonly unknown[],
  fn: (api: ControlApi, signal: AbortSignal) => Promise<T>,
  opts: { slow?: boolean; enabled?: boolean } = {},
) {
  const { api, baseUrl } = useControl()
  const { interval } = useSettings()
  return useQuery({
    queryKey: [...qk.control(baseUrl), ...key],
    queryFn: ({ signal }) => fn(api as ControlApi, signal),
    enabled: api !== null && (opts.enabled ?? true),
    refetchInterval: interval ? (opts.slow ? Math.max(interval * 3, 6000) : Math.max(interval, 3000)) : false,
    placeholderData: keepPreviousData,
    retry,
  })
}

export const useControlOverview = () => useControlQuery(["overview"], (a, s) => a.overview(s))
export const useNodes = () => useControlQuery(["nodes"], (a, s) => a.nodes(s))
export const useNode = (id: string) => useControlQuery(["node", id], (a, s) => a.node(id, s))
export const useClaims = (status?: string) => useControlQuery(["claims", status ?? "all"], (a, s) => a.claims(status, s))
export const useTokens = () => useControlQuery(["tokens"], (a, s) => a.tokens(s), { slow: true })
export const useAudit = (q: { limit?: number; node_id?: string } = {}) =>
  useControlQuery(["audit", q], (a, s) => a.audit(q, s), { slow: true })
export const useProjection = () => useControlQuery(["projection"], (a, s) => a.projection(s))
export const useCrl = () => useControlQuery(["crl"], (a, s) => a.crl(s), { slow: true })
