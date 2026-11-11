import { useQuery } from "@tanstack/react-query"

import { useSettings } from "@/providers/settings-provider"

import { cloud } from "./api"
import { useWorkspace } from "./session"

export function useGateways() {
  const ws = useWorkspace()
  const { interval } = useSettings()
  return useQuery({
    queryKey: ["cloud", "gateways", ws.id],
    queryFn: ({ signal }) => cloud.gateways(ws.id, signal),
    refetchInterval: interval ? Math.max(interval, 5000) : false,
  })
}

export function useMembers() {
  const ws = useWorkspace()
  return useQuery({ queryKey: ["cloud", "members", ws.id], queryFn: ({ signal }) => cloud.members(ws.id, signal) })
}

export function useGateway(id: string) {
  const ws = useWorkspace()
  const { interval } = useSettings()
  return useQuery({
    queryKey: ["cloud", "gateway", ws.id, id],
    queryFn: ({ signal }) => cloud.gateway(ws.id, id, signal),
    refetchInterval: interval ? Math.max(interval, 5000) : false,
  })
}

export function useGatewayTokens(enabled = true) {
  const ws = useWorkspace()
  return useQuery({
    queryKey: ["cloud", "gateway-tokens", ws.id],
    queryFn: ({ signal }) => cloud.gatewayTokens(ws.id, signal),
    enabled,
  })
}

function useLive<T>(key: string, fn: (ws: string, signal: AbortSignal) => Promise<T>, enabled = true, extra: string[] = []) {
  const ws = useWorkspace()
  const { interval } = useSettings()
  return useQuery({
    queryKey: ["cloud", key, ws.id, ...extra],
    queryFn: ({ signal }) => fn(ws.id, signal),
    refetchInterval: interval ? Math.max(interval, 5000) : false,
    enabled,
  })
}

export const useOffers = (mine = false) => useLive(mine ? "my-offers" : "offers", (ws, s) => cloud.offers(ws, mine, s))
export const useRentals = () => useLive("rentals", cloud.rentals)
export const useJobs = () => useLive("jobs", cloud.jobs)
export const useJob = (id: string) => useLive("job", (ws, s) => cloud.job(ws, id, s), true, [id])
/** The built-in job templates: static, so fetched once per workspace. */
export function useJobTemplates() {
  const ws = useWorkspace()
  return useQuery({
    queryKey: ["cloud", "job-templates", ws.id],
    queryFn: ({ signal }) => cloud.jobTemplates(ws.id, signal),
    staleTime: Infinity,
  })
}
export const useBilling = () => useLive("billing", cloud.billing)
export const useUsage = (month: string) => useLive("usage", (ws, s) => cloud.usage(ws, month, s), true, [month])
export const useListableNodes = (enabled: boolean) => useLive("listable-nodes", cloud.listableNodes, enabled)

export function useApiKeys(enabled = true) {
  const ws = useWorkspace()
  return useQuery({ queryKey: ["cloud", "api-keys", ws.id], queryFn: ({ signal }) => cloud.apiKeys(ws.id, signal), enabled })
}

export function usePlatformSummary(enabled: boolean) {
  return useQuery({ queryKey: ["cloud", "platform"], queryFn: ({ signal }) => cloud.platformSummary(signal), enabled })
}
