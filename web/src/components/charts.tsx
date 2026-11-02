import type { ReactNode } from "react"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import { formatNumber, formatTimestamp, type TimeZoneMode } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useSettings } from "@/providers/settings-provider"

// Categorical slots in validated order. Assign in order; never cycle past 8.
export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
]

/** Stable color for an entity (color follows the entity, never its rank). */
export function seriesColorFor(name: string, universe: string[]): string {
  const sorted = [...new Set(universe)].sort()
  const idx = sorted.indexOf(name)
  return idx >= 0 && idx < SERIES.length ? SERIES[idx] : "var(--muted-foreground)"
}

export interface SeriesSpec {
  key: string
  label: string
  color: string
}

function configFrom(series: SeriesSpec[]): ChartConfig {
  return Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }]))
}

function timeTick(tz: TimeZoneMode) {
  return (v: number) => formatTimestamp(v, tz, { date: false, seconds: true }).replace(" UTC", "")
}

const axisProps = {
  tickLine: false,
  axisLine: false,
  tickMargin: 8,
  fontSize: 11,
} as const

/** Time-series area (stacked or overlaid), crosshair tooltip over all series. */
export function TimeAreaChart<T extends { t: number }>({
  data,
  series,
  stacked,
  height = 220,
  valueFormatter = (v: number) => formatNumber(v),
  axisFormatter,
  yWidth = 44,
  className,
  emptyLabel = "Collecting samples…",
}: {
  data: T[]
  series: SeriesSpec[]
  stacked?: boolean
  height?: number
  valueFormatter?: (v: number) => string
  axisFormatter?: (v: number) => string
  yWidth?: number
  className?: string
  emptyLabel?: ReactNode
}) {
  const { settings } = useSettings()
  if (data.length < 2) return <ChartEmpty height={height} label={emptyLabel} />
  return (
    <ChartContainer config={configFrom(series)} className={cn("aspect-auto w-full", className)} style={{ height }}>
      <AreaChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={timeTick(settings.timeZone)} minTickGap={48} {...axisProps} />
        <YAxis width={yWidth} tickFormatter={(v: number) => (axisFormatter ?? valueFormatter)(v)} allowDecimals {...axisProps} />
        <ChartTooltip
          cursor={{ strokeWidth: 1 }}
          content={
            <ChartTooltipContent
              indicator="line"
              labelFormatter={(_, payload) => {
                const t = (payload?.[0]?.payload as { t?: number } | undefined)?.t
                return t ? formatTimestamp(t, settings.timeZone, { date: false }) : ""
              }}
              formatter={(value, name) => (
                <div className="flex w-full items-center justify-between gap-4">
                  <span className="text-muted-foreground">{series.find((s) => s.key === name)?.label ?? name}</span>
                  <span className="font-mono font-medium tabular-nums">{valueFormatter(Number(value))}</span>
                </div>
              )}
            />
          }
        />
        {series.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {series.map((s) => (
          <Area
            key={s.key}
            dataKey={s.key}
            type="monotone"
            stackId={stacked ? "a" : undefined}
            strokeOpacity={stacked && data.every((d) => !Number((d as Record<string, unknown>)[s.key])) ? 0 : 1}
            stroke={`var(--color-${s.key})`}
            fill={`var(--color-${s.key})`}
            fillOpacity={stacked ? 0.18 : 0.1}
            strokeWidth={2}
            isAnimationActive={false}
            connectNulls
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }}
          />
        ))}
      </AreaChart>
    </ChartContainer>
  )
}

export function TimeLineChart<T extends { t: number }>({
  data,
  series,
  height = 220,
  valueFormatter = (v: number) => formatNumber(v),
  axisFormatter,
  yWidth = 48,
  className,
  emptyLabel = "Collecting samples…",
}: {
  data: T[]
  series: SeriesSpec[]
  height?: number
  valueFormatter?: (v: number) => string
  axisFormatter?: (v: number) => string
  yWidth?: number
  className?: string
  emptyLabel?: ReactNode
}) {
  const { settings } = useSettings()
  if (data.length < 2) return <ChartEmpty height={height} label={emptyLabel} />
  return (
    <ChartContainer config={configFrom(series)} className={cn("aspect-auto w-full", className)} style={{ height }}>
      <LineChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={timeTick(settings.timeZone)} minTickGap={48} {...axisProps} />
        <YAxis width={yWidth} tickFormatter={(v: number) => (axisFormatter ?? valueFormatter)(v)} {...axisProps} />
        <ChartTooltip
          cursor={{ strokeWidth: 1 }}
          content={
            <ChartTooltipContent
              indicator="line"
              labelFormatter={(_, payload) => {
                const t = (payload?.[0]?.payload as { t?: number } | undefined)?.t
                return t ? formatTimestamp(t, settings.timeZone, { date: false }) : ""
              }}
              formatter={(value, name) => (
                <div className="flex w-full items-center justify-between gap-4">
                  <span className="text-muted-foreground">{series.find((s) => s.key === name)?.label ?? name}</span>
                  <span className="font-mono font-medium tabular-nums">
                    {value === null || value === undefined ? "—" : valueFormatter(Number(value))}
                  </span>
                </div>
              )}
            />
          }
        />
        {series.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {series.map((s) => (
          <Line
            key={s.key}
            dataKey={s.key}
            type="monotone"
            stroke={`var(--color-${s.key})`}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }}
          />
        ))}
      </LineChart>
    </ChartContainer>
  )
}

/** Vertical columns for a distribution (one series → one color). */
export function ColumnChart({
  data,
  xKey,
  yKey,
  label,
  color = "var(--series-1)",
  height = 200,
  valueFormatter = (v: number) => formatNumber(v),
  className,
}: {
  data: Record<string, string | number>[]
  xKey: string
  yKey: string
  label: string
  color?: string
  height?: number
  valueFormatter?: (v: number) => string
  className?: string
}) {
  if (!data.length) return <ChartEmpty height={height} label="No observations yet" />
  return (
    <ChartContainer
      config={{ [yKey]: { label, color } }}
      className={cn("aspect-auto w-full", className)}
      style={{ height }}
    >
      <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey={xKey} {...axisProps} interval="preserveStartEnd" />
        <YAxis width={44} tickFormatter={(v: number) => valueFormatter(v)} {...axisProps} />
        <ChartTooltip
          cursor={{ fillOpacity: 0.5 }}
          content={
            <ChartTooltipContent
              formatter={(value) => (
                <div className="flex w-full items-center justify-between gap-4">
                  <span className="text-muted-foreground">{label}</span>
                  <span className="font-mono font-medium tabular-nums">{valueFormatter(Number(value))}</span>
                </div>
              )}
            />
          }
        />
        <Bar dataKey={yKey} fill={`var(--color-${yKey})`} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
      </BarChart>
    </ChartContainer>
  )
}

/** Stacked daily columns by category (e.g. cost by model). */
export function StackedColumnChart({
  data,
  xKey,
  series,
  height = 240,
  valueFormatter = (v: number) => formatNumber(v),
  xFormatter,
  className,
}: {
  data: Record<string, string | number>[]
  xKey: string
  series: SeriesSpec[]
  height?: number
  valueFormatter?: (v: number) => string
  xFormatter?: (v: string) => string
  className?: string
}) {
  if (!data.length) return <ChartEmpty height={height} label="No data in this window" />
  return (
    <ChartContainer config={configFrom(series)} className={cn("aspect-auto w-full", className)} style={{ height }}>
      <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey={xKey} tickFormatter={xFormatter} {...axisProps} minTickGap={24} />
        <YAxis width={56} tickFormatter={(v: number) => valueFormatter(v)} {...axisProps} />
        <ChartTooltip
          cursor={{ fillOpacity: 0.4 }}
          content={
            <ChartTooltipContent
              indicator="line"
              labelFormatter={(l) => (xFormatter ? xFormatter(String(l)) : String(l))}
              formatter={(value, name) => (
                <div className="flex w-full items-center justify-between gap-4">
                  <span className="text-muted-foreground">{series.find((s) => s.key === name)?.label ?? name}</span>
                  <span className="font-mono font-medium tabular-nums">{valueFormatter(Number(value))}</span>
                </div>
              )}
            />
          }
        />
        {series.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
        {series.map((s, i) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            stackId="a"
            fill={`var(--color-${s.key})`}
            maxBarSize={24}
            isAnimationActive={false}
            // 2px surface gap between stacked segments; rounded data-end on top only.
            stroke="var(--card)"
            strokeWidth={1}
            radius={i === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
          />
        ))}
      </BarChart>
    </ChartContainer>
  )
}

/** Ranked horizontal bars as HTML — label, value, thin bar. One color. */
export function BarList({
  items,
  valueFormatter = (v: number) => formatNumber(v),
  color = "var(--series-1)",
  className,
  max: maxOverride,
  onSelect,
}: {
  items: { key: string; label: ReactNode; value: number; hint?: ReactNode; color?: string }[]
  valueFormatter?: (v: number) => string
  color?: string
  className?: string
  max?: number
  onSelect?: (key: string) => void
}) {
  const max = maxOverride ?? Math.max(1, ...items.map((i) => i.value))
  if (!items.length) return <div className="py-6 text-center text-sm text-muted-foreground">No data yet</div>
  return (
    <ul className={cn("space-y-3", className)}>
      {items.map((it) => {
        const inner = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <span className="min-w-0 truncate">{it.label}</span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                {valueFormatter(it.value)}
                {it.hint && <span className="ml-1.5">{it.hint}</span>}
              </span>
            </div>
            <div className="mt-1.5 h-2 w-full rounded-full bg-muted">
              <div
                className="h-full rounded-r-full rounded-l-[2px] transition-[width] duration-500"
                style={{ width: `${Math.max(0.5, (it.value / max) * 100)}%`, background: it.color ?? color }}
              />
            </div>
          </>
        )
        return (
          <li key={it.key}>
            {onSelect ? (
              <button
                type="button"
                className="w-full rounded-md text-left focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
                onClick={() => onSelect(it.key)}
              >
                {inner}
              </button>
            ) : (
              inner
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** One whole split into parts — thin segmented bar + legend. */
export function SegmentedBar({
  parts,
  className,
  valueFormatter = (v: number) => formatNumber(v),
}: {
  parts: { key: string; label: string; value: number; color: string }[]
  className?: string
  valueFormatter?: (v: number) => string
}) {
  const total = parts.reduce((a, p) => a + p.value, 0)
  return (
    <div className={cn("space-y-2.5", className)}>
      <div
        className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full"
        role="img"
        aria-label={parts.map((p) => `${p.label} ${total ? Math.round((p.value / total) * 100) : 0}%`).join(", ")}
      >
        {total === 0 ? (
          <div className="h-full w-full rounded-full bg-muted" />
        ) : (
          parts
            .filter((p) => p.value > 0)
            .map((p) => (
              <div
                key={p.key}
                className="h-full first:rounded-l-full last:rounded-r-full"
                style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
                title={`${p.label}: ${valueFormatter(p.value)}`}
              />
            ))
        )}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {parts.map((p) => (
          <li key={p.key} className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px]" style={{ background: p.color }} aria-hidden />
            <span className="text-muted-foreground">{p.label}</span>
            <span className="font-mono tabular-nums">{valueFormatter(p.value)}</span>
            {total > 0 && <span className="text-muted-foreground">({Math.round((p.value / total) * 100)}%)</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ChartEmpty({ height, label }: { height: number; label: ReactNode }) {
  return (
    <div
      className="flex w-full items-center justify-center rounded-lg border border-dashed text-xs text-muted-foreground"
      style={{ height }}
    >
      {label}
    </div>
  )
}
