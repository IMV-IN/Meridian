import {
  Circle,
  CircleCheck,
  CircleDashed,
  CircleX,
  Info,
  Loader,
  Moon,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export type Tone = "good" | "warning" | "serious" | "critical" | "info" | "neutral" | "muted"

const TONE_ICON: Record<Tone, LucideIcon> = {
  good: CircleCheck,
  warning: TriangleAlert,
  serious: OctagonAlert,
  critical: CircleX,
  info: Info,
  neutral: Circle,
  muted: Moon,
}

// Status color lives on the icon + a faint wash; the label stays in text ink
// so meaning never depends on color alone.
const TONE_CLASS: Record<Tone, string> = {
  good: "[--tone:var(--status-good)]",
  warning: "[--tone:var(--status-warning)]",
  serious: "[--tone:var(--status-serious)]",
  critical: "[--tone:var(--status-critical)]",
  info: "[--tone:var(--series-1)]",
  neutral: "[--tone:var(--muted-foreground)]",
  muted: "[--tone:var(--muted-foreground)]",
}

export function toneColor(tone: Tone): string {
  return {
    good: "var(--status-good)",
    warning: "var(--status-warning)",
    serious: "var(--status-serious)",
    critical: "var(--status-critical)",
    info: "var(--series-1)",
    neutral: "var(--muted-foreground)",
    muted: "var(--muted-foreground)",
  }[tone]
}

interface StatusBadgeProps {
  tone: Tone
  children: ReactNode
  icon?: LucideIcon | null
  className?: string
  spinning?: boolean
  size?: "sm" | "md"
  title?: string
}

export function StatusBadge({ tone, children, icon, className, spinning, size = "md", title }: StatusBadgeProps) {
  const Icon = icon === null ? null : (icon ?? (spinning ? Loader : TONE_ICON[tone]))
  return (
    <span
      title={title}
      className={cn(
        TONE_CLASS[tone],
        "inline-flex shrink-0 items-center gap-1.5 rounded-md border font-medium whitespace-nowrap text-foreground",
        "border-[color-mix(in_oklab,var(--tone)_28%,transparent)] bg-[color-mix(in_oklab,var(--tone)_10%,transparent)]",
        size === "sm" ? "h-5 px-1.5 text-[11px]" : "h-6 px-2 text-xs",
        className,
      )}
    >
      {Icon && (
        <Icon
          aria-hidden
          className={cn("shrink-0 text-(--tone)", size === "sm" ? "size-3" : "size-3.5", spinning && "animate-spin")}
        />
      )}
      {children}
    </span>
  )
}

/** Small colored dot + label, for dense rows. */
export function StatusDot({ tone, label, pulse }: { tone: Tone; label?: ReactNode; pulse?: boolean }) {
  return (
    <span className={cn(TONE_CLASS[tone], "inline-flex items-center gap-2")}>
      <span className="relative inline-flex size-2">
        {pulse && <span className="animate-pulse-ring absolute inset-0 rounded-full bg-(--tone)" />}
        <span className="relative inline-flex size-2 rounded-full bg-(--tone)" />
      </span>
      {label !== undefined && <span className="text-foreground">{label}</span>}
    </span>
  )
}

// ── Domain mappings ───────────────────────────────────────────────────────

export function httpStatusTone(code: number): Tone {
  if (code >= 500 || code === 499) return "critical"
  if (code === 429) return "warning"
  if (code >= 400) return "serious"
  if (code >= 200 && code < 300) return "good"
  return "neutral"
}

export const BACKEND_HEALTH_META = {
  healthy: { tone: "good" as Tone, label: "Healthy" },
  degraded: { tone: "warning" as Tone, label: "Half-open" },
  unhealthy: { tone: "critical" as Tone, label: "Unhealthy" },
  idle: { tone: "muted" as Tone, label: "Idle" },
  circuit_open: { tone: "critical" as Tone, label: "Circuit open" },
}

export const NODE_STATUS_META: Record<string, { tone: Tone; label: string }> = {
  online: { tone: "good", label: "Online" },
  offline: { tone: "serious", label: "Lease expired" },
  revoked: { tone: "critical", label: "Revoked" },
  enrolled: { tone: "info", label: "Awaiting session" },
}

export function enginePhaseTone(phase: string): Tone {
  switch (phase) {
    case "Ready":
      return "good"
    case "Pending":
    case "Pulling":
    case "Starting":
    case "Warming":
      return "info"
    case "Draining":
      return "warning"
    case "Stopped":
      return "muted"
    case "Failed":
    case "Quarantined":
    case "Orphaned":
      return "critical"
    default:
      return "neutral"
  }
}

export function isTransitionalPhase(phase: string) {
  return ["Pending", "Pulling", "Starting", "Warming", "Draining"].includes(phase)
}

export { CircleDashed }
