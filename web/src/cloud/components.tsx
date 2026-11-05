import { StatusBadge } from "@/components/status-badge"

import type { GatewaySummary } from "./api"

export function GatewayStatusBadge({ g }: { g: GatewaySummary }) {
  if (!g.online) return <StatusBadge tone="serious" size="sm">Offline</StatusBadge>
  if (g.backends_total > 0 && g.backends_healthy < g.backends_total)
    return (
      <StatusBadge tone="warning" size="sm">
        Degraded
      </StatusBadge>
    )
  return (
    <StatusBadge tone="good" size="sm">
      Online
    </StatusBadge>
  )
}
