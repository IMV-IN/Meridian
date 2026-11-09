import type { LucideIcon } from "lucide-react"
import { useId, type ReactNode } from "react"

import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

import type { Tone } from "./status-badge"
import { toneColor } from "./status-badge"

/** Inline SVG sparkline: 1.5px line, 10% wash, accent end-dot. */
export function Sparkline({
  values,
  color = "var(--series-1)",
  height = 32,
  className,
  min: minOverride,
}: {
  values: (number | null)[]
  color?: string
  height?: number
  className?: string
  min?: number
}) {
  const gid = useId().replace(/:/g, "")
  const pts = values.map((v, i) => ({ i, v })).filter((p): p is { i: number; v: number } => p.v !== null && Number.isFinite(p.v))
  if (pts.length < 2) {
    return <div className={cn("h-8 w-full", className)} style={{ height }} aria-hidden />
  }
  const w = 120
  const h = height
  const max = Math.max(...pts.map((p) => p.v))
  const min = minOverride ?? Math.min(0, ...pts.map((p) => p.v))
  const span = max - min || 1
  const n = values.length - 1 || 1
  const x = (i: number) => (i / n) * w
  const y = (v: number) => h - 3 - ((v - min) / span) * (h - 6)
  const line = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")
  const area = `${line} L${x(pts[pts.length - 1].i).toFixed(1)},${h} L${x(pts[0].i).toFixed(1)},${h} Z`
  const last = pts[pts.length - 1]
  // The line stretches to any width (preserveAspectRatio="none"), so the end
  // dot is drawn in HTML: a circle inside the stretched SVG becomes an oval.
  // px-1 keeps the dot inside the card.
  return (
    <div className={cn("w-full px-1", className)} style={{ height }} aria-hidden>
      <div className="relative size-full">
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 size-full overflow-visible">
          <defs>
            <linearGradient id={`spark-${gid}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.16} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#spark-${gid})`} />
          <path d={line} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        </svg>
        <span
          className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px] border-card"
          style={{ left: `${(x(last.i) / w) * 100}%`, top: `${(y(last.v) / h) * 100}%`, background: color }}
        />
      </div>
    </div>
  )
}

export interface StatCardProps {
  label: string
  value: ReactNode
  unit?: ReactNode
  hint?: ReactNode
  icon?: LucideIcon
  tone?: Tone
  trend?: (number | null)[]
  trendColor?: string
  loading?: boolean
  info?: ReactNode
  className?: string
  footer?: ReactNode
}

export function StatCard({
  label,
  value,
  unit,
  hint,
  icon: Icon,
  tone,
  trend,
  trendColor,
  loading,
  info,
  className,
  footer,
}: StatCardProps) {
  return (
    <Card className={cn("relative gap-0 overflow-hidden py-0", className)}>
      <div className="flex flex-col gap-2 p-4 pb-3">
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-muted-foreground">
            {Icon && <Icon className="size-3.5 shrink-0" aria-hidden />}
            {info ? (
              <Tooltip>
                <TooltipTrigger className="text-left leading-tight underline decoration-dotted decoration-muted-foreground/50 underline-offset-4">
                  {label}
                </TooltipTrigger>
                <TooltipContent className="max-w-64">{info}</TooltipContent>
              </Tooltip>
            ) : (
              <span className="leading-tight">{label}</span>
            )}
          </span>
          {tone && tone !== "neutral" && (
            <span className="size-2 shrink-0 rounded-full" style={{ background: toneColor(tone) }} aria-hidden />
          )}
        </div>
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <div className="flex items-baseline gap-1">
            <span className="text-[1.65rem] leading-none font-semibold tracking-tight">{value}</span>
            {unit && <span className="text-sm font-medium text-muted-foreground">{unit}</span>}
          </div>
        )}
        {hint && <div className="min-h-4 text-xs leading-snug text-muted-foreground">{hint}</div>}
      </div>
      {trend && (
        <div className="-mt-1 px-0">
          <Sparkline values={trend} color={trendColor} height={30} />
        </div>
      )}
      {footer && <div className="border-t px-4 py-2 text-xs text-muted-foreground">{footer}</div>}
    </Card>
  )
}

/** Utilization meter: fill carries severity; track is a light step of the same hue. */
export function Meter({
  value,
  warnAt = 0.8,
  critAt = 1,
  className,
  label,
  size = "md",
}: {
  value: number | null
  warnAt?: number
  critAt?: number
  className?: string
  label?: string
  size?: "sm" | "md"
}) {
  const v = value === null || !Number.isFinite(value) ? 0 : Math.max(0, value)
  const color = v >= critAt ? "var(--status-critical)" : v >= warnAt ? "var(--status-warning)" : "var(--series-1)"
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      className={cn("w-full overflow-hidden rounded-full", size === "sm" ? "h-1.5" : "h-2", className)}
      style={{ background: `color-mix(in oklab, ${color} 16%, transparent)` }}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500 ease-out"
        style={{ width: `${Math.min(100, v * 100)}%`, background: color }}
      />
    </div>
  )
}
