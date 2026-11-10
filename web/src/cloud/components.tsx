import { StatusBadge } from "@/components/status-badge"

import type { GatewaySummary, JobStatus } from "./api"

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

const JOB_TONE: Record<JobStatus, "good" | "warning" | "critical" | "info" | "neutral" | "muted"> = {
  queued: "warning",
  scheduled: "info",
  running: "info",
  succeeded: "good",
  failed: "critical",
  cancelled: "muted",
}

const JOB_LABEL: Record<JobStatus, string> = {
  queued: "Queued",
  scheduled: "Starting",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <StatusBadge tone={JOB_TONE[status]} size="sm" spinning={status === "scheduled"}>
      {JOB_LABEL[status]}
    </StatusBadge>
  )
}

const PHASE_TONE: Record<string, "good" | "critical" | "info" | "muted" | "neutral"> = {
  Succeeded: "good",
  Failed: "critical",
  Cancelled: "muted",
  Running: "info",
}

export function PhaseBadge({ phase }: { phase: string }) {
  return (
    <StatusBadge tone={PHASE_TONE[phase] ?? "neutral"} size="sm">
      {phase}
    </StatusBadge>
  )
}
