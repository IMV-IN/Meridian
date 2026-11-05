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
