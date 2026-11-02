import { ArrowLeft, ExternalLink, HeartPulse, MessageSquareText, Server, Timer, Zap } from "lucide-react"
import { useMemo } from "react"
import { Link, useNavigate, useParams } from "react-router"

import { BarList, ColumnChart, TimeAreaChart, TimeLineChart } from "@/components/charts"
import { CopyText } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { Hint, TimeAgo } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel, TagList } from "@/components/page"
import { Meter, StatCard } from "@/components/stat"
import { BACKEND_HEALTH_META, StatusBadge, httpStatusTone } from "@/components/status-badge"
import { EmptyState, ErrorState, LoadingGrid } from "@/components/states"
import { Button } from "@/components/ui/button"
import { useConfig, useMetrics, useRecentRequests, useStatus } from "@/hooks/queries"
import type { BackendStatus, ConfigSummary, RequestRecord } from "@/lib/api/types"
import {
  backendLabel,
  formatMs,
  formatMsShort,
  formatNumber,
  formatPercent,
  formatSeconds,
  titleCase,
  formatEwma,
} from "@/lib/format"
import { bucketCounts, histogram, quantile, sum, sumBy } from "@/lib/prometheus"
import { backendHealth } from "@/lib/telemetry"
import { useTelemetry } from "@/providers/telemetry-provider"

interface Membership {
  label: string
  detail: string
  tone: "good" | "info" | "warning" | "neutral"
}

function memberships(b: BackendStatus, cfg: ConfigSummary | undefined, canaryWeight?: number): Membership[] {
  if (!cfg) return []
  const tags = new Set(b.tags)
  const has = (t: string[]) => t.length > 0 && t.every((x) => tags.has(x))
  const out: Membership[] = []
  if (cfg.tiering.enabled) {
    for (const [tier, t] of Object.entries(cfg.tiering.tiers)) {
      if (has(t)) out.push({ label: `Tier · ${titleCase(tier)}`, detail: `tags ${t.join(", ")}`, tone: "info" })
    }
  }
  if (cfg.canary.enabled) {
    if (has(cfg.canary.canary_tags))
      out.push({ label: "Canary pool", detail: `receives ${canaryWeight ?? "—"}% of split traffic`, tone: "warning" })
    if (has(cfg.canary.stable_tags))
      out.push({ label: "Stable pool", detail: `receives ${canaryWeight === undefined ? "—" : 100 - canaryWeight}% of split traffic`, tone: "good" })
  }
  if (cfg.isolation.mode === "dedicated") {
    for (const [org, t] of Object.entries(cfg.isolation.pools)) {
      if (has(t)) out.push({ label: `Dedicated to ${org}`, detail: "other orgs can never land here", tone: "neutral" })
    }
  }
  if (b.name.startsWith("managed:"))
    out.push({ label: "Managed engine", detail: "registered from the control-plane projection", tone: "info" })
  return out
}

const reqColumns: ColumnDef<RequestRecord, unknown>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    cell: ({ getValue }) => <TimeAgo value={getValue() as string} className="text-xs" />,
  },
  {
    accessorKey: "status_code",
    header: "Status",
    cell: ({ row }) => (
      <StatusBadge tone={httpStatusTone(row.original.status_code)} size="sm">
        {row.original.status_code}
      </StatusBadge>
    ),
  },
  { accessorKey: "org_id", header: "Org", cell: ({ getValue }) => (getValue() as string) ?? <span className="text-muted-foreground">—</span> },
  { accessorKey: "tier", header: "Tier", cell: ({ getValue }) => (getValue() as string) ?? "—" },
  {
    accessorKey: "stream",
    header: "Mode",
    cell: ({ getValue }) => <span className="text-xs">{getValue() ? "stream" : "sync"}</span>,
  },
  {
    accessorKey: "latency_ms",
    header: "Latency",
    meta: { align: "right" },
    cell: ({ getValue }) => formatMs(getValue() as number),
  },
  {
    accessorKey: "request_id",
    header: "Request ID",
    cell: ({ getValue }) => <span className="font-mono text-xs text-muted-foreground">{getValue() as string}</span>,
  },
]

export default function BackendDetailPage() {
  const { name: rawName = "" } = useParams()
  const name = decodeURIComponent(rawName)
  const navigate = useNavigate()
  const status = useStatus()
  const config = useConfig()
  const metrics = useMetrics()
  const records = useRecentRequests()
  const { backends, traffic } = useTelemetry()

  const b = status.data?.backends.find((x) => x.name === name)
  const series = useMemo(() => backends[name] ?? [], [backends, name])
  const rpsSeries = useMemo(() => traffic.map((p) => ({ t: p.t, rps: p.byBackend[name] ?? 0 })), [traffic, name])
  const recent = useMemo(() => (records.data ?? []).filter((r) => r.chosen_backend === name), [records.data, name])

  const m = useMemo(() => {
    if (!metrics.parsed) return null
    const f = { backend: name }
    const total = sum(metrics.parsed, "meridian_requests_total", f)
    const byStatus = sumBy(metrics.parsed, "meridian_requests_total", "status", f)
    const errors = Object.entries(byStatus)
      .filter(([s]) => Number(s) >= 500)
      .reduce((a, [, n]) => a + n, 0)
    const h = histogram(metrics.parsed, "meridian_request_latency_ms", f)
    return {
      total,
      byStatus,
      errorRate: total ? errors / total : null,
      p50: quantile(0.5, h),
      p95: quantile(0.95, h),
      p99: quantile(0.99, h),
      dist: bucketCounts(h).map((x) => ({ bucket: x.label, count: x.count })),
      retries: sum(metrics.parsed, "meridian_upstream_retries_total", f),
    }
  }, [metrics.parsed, name])

  if (status.isError && !status.data) return <Page><ErrorState error={status.error} onRetry={() => status.refetch()} /></Page>
  if (!status.data) return <Page><LoadingGrid count={4} /></Page>
  if (!b)
    return (
      <Page>
        <EmptyState
          icon={Server}
          title={`Backend “${name}” is not registered`}
          description="It may have been removed by a config reload, or a managed engine left the serving projection."
        >
          <Button variant="outline" size="sm" asChild>
            <Link to="/backends">
              <ArrowLeft data-icon="inline-start" /> All backends
            </Link>
          </Button>
        </EmptyState>
      </Page>
    )

  const h = BACKEND_HEALTH_META[backendHealth(b)]
  const label = backendLabel(b.name)
  const bc = config.data?.backends.find((x) => x.name === b.name)
  const cfg = config.data
  const pools = memberships(b, cfg, status.data.canary?.weight)
  const timeouts = cfg ? { ...cfg.timeouts, ...(bc?.timeout_override ?? {}) } : null

  return (
    <Page>
      <PageHeader
        eyebrow={label.managed ? "Managed backend" : "Backend"}
        title={
          <span className="flex flex-wrap items-center gap-3">
            <span className="break-all">{label.primary}</span>
            <StatusBadge tone={h.tone}>{h.label}</StatusBadge>
          </span>
        }
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              {b.engine} · {b.model ? <span className="font-mono text-xs">{b.model}</span> : "any model"}
            </span>
            <CopyText value={b.url} />
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link to={`/requests?backend=${encodeURIComponent(b.name)}`}>Requests</Link>
            </Button>
            <Button size="sm" onClick={() => navigate(`/playground?model=${encodeURIComponent(b.model)}`)} disabled={!b.model}>
              <MessageSquareText data-icon="inline-start" /> Try in playground
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard label="In flight" icon={Zap} value={formatNumber(b.inflight)} hint={`cost units in flight ${formatNumber(b.inflight_cost)}`} trend={series.map((p) => p.inflight)} />
        <StatCard
          label="EWMA latency"
          icon={Timer}
          value={formatEwma(b.ewma_latency_ms)}
          hint="α = 0.3 over completed requests"
          trend={series.map((p) => p.ewma)}
          trendColor="var(--series-2)"
          info="Exponentially-weighted moving average of end-to-end latency; token-aware routing uses it as the latency factor."
        />
        <StatCard
          label="Latency p95"
          icon={Timer}
          value={formatMs(m?.p95)}
          hint={`p50 ${formatMs(m?.p50)} · p99 ${formatMs(m?.p99)}`}
          info="Since gateway start, from the per-backend latency histogram."
        />
        <StatCard
          label="Requests"
          icon={HeartPulse}
          value={formatNumber(m?.total ?? null)}
          hint={`5xx rate ${formatPercent(m?.errorRate ?? null)} · ${formatNumber(m?.retries ?? 0)} retries`}
          tone={m?.errorRate ? (m.errorRate > 0.05 ? "critical" : m.errorRate > 0.01 ? "warning" : "good") : "neutral"}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Throughput" description="Requests per second routed here">
          <TimeAreaChart data={rpsSeries} height={200} series={[{ key: "rps", label: "req/s", color: "var(--series-1)" }]} valueFormatter={(v) => formatNumber(v, { digits: 1 })} />
        </Panel>
        <Panel title="EWMA latency" description="Sampled each refresh">
          <TimeLineChart data={series} height={200} series={[{ key: "ewma", label: "EWMA", color: "var(--series-2)" }]} valueFormatter={formatMs} axisFormatter={formatMsShort} />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Health & circuit breaker">
          <div className="space-y-4">
            <KeyValueGrid
              items={[
                { label: "Active health", value: b.healthy ? "Passing" : "Failing" },
                { label: "Scale-to-zero", value: b.idle ? "Idle (woken on demand)" : bc?.idle_timeout_min ? `after ${bc.idle_timeout_min} min idle` : "Off" },
                {
                  label: "Circuit",
                  value: b.circuit ? (
                    <StatusBadge
                      tone={b.circuit.state === "closed" ? "good" : b.circuit.state === "open" ? "critical" : "warning"}
                      size="sm"
                    >
                      {titleCase(b.circuit.state)}
                    </StatusBadge>
                  ) : (
                    "Disabled"
                  ),
                },
                { label: "Consecutive failures", value: b.circuit ? `${b.circuit.consecutive_failures} / ${cfg?.resilience.circuit_breaker.failure_threshold ?? "—"}` : "—" },
                { label: "Health endpoint", value: <span className="font-mono text-xs">{bc?.health_endpoint ?? "/v1/models"}</span> },
                {
                  label: "Probe cadence",
                  value: cfg ? `every ${formatSeconds(cfg.health.interval_s)} · fail ×${cfg.health.fail_threshold}` : "—",
                },
              ]}
            />
          </div>
        </Panel>
        <Panel title="Telemetry" description={bc?.telemetry ? `JSON adapter · every ${formatSeconds(bc.telemetry.interval_s)}` : "No adapter configured"}>
          {b.queue_depth === null && b.gpu_mem_util === null && b.tokens_per_sec === null ? (
            <p className="text-sm text-muted-foreground">
              Capacity signals (queue depth, GPU memory, tokens/s) appear here when <code className="text-xs">backends[].telemetry</code> is configured. Capacity-aware routing adds
              them as penalties.
            </p>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">GPU memory</span>
                  <span className="tabular">{formatPercent(b.gpu_mem_util)}</span>
                </div>
                <Meter value={b.gpu_mem_util} warnAt={0.85} critAt={0.95} label="GPU memory" />
              </div>
              <KeyValueGrid
                items={[
                  { label: "Queue depth", value: formatNumber(b.queue_depth) },
                  { label: "Tokens / s", value: formatNumber(b.tokens_per_sec) },
                ]}
              />
            </div>
          )}
        </Panel>
        <Panel title="Routing membership" description="Pools whose tag sets this backend satisfies">
          <div className="space-y-3">
            <TagList tags={b.tags} max={12} />
            {pools.length === 0 ? (
              <p className="text-sm text-muted-foreground">Not part of any tier, canary or isolation pool — eligible for default routing.</p>
            ) : (
              <ul className="space-y-2">
                {pools.map((p) => (
                  <li key={p.label} className="flex items-start justify-between gap-3 text-sm">
                    <StatusBadge tone={p.tone === "neutral" ? "neutral" : p.tone} size="sm" icon={null}>
                      {p.label}
                    </StatusBadge>
                    <span className="text-right text-xs text-muted-foreground">{p.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Latency distribution" description="Histogram buckets since gateway start (ms)">
          <ColumnChart data={m?.dist ?? []} xKey="bucket" yKey="count" label="Requests" height={210} />
        </Panel>
        <Panel title="Responses by status" description="Since gateway start">
          <BarList
            items={Object.entries(m?.byStatus ?? {})
              .sort((a, c) => c[1] - a[1])
              .map(([s, n]) => ({
                key: s,
                label: (
                  <StatusBadge tone={httpStatusTone(Number(s))} size="sm">
                    {s}
                  </StatusBadge>
                ),
                value: n,
              }))}
          />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Recent requests" description={`${recent.length} of the last ${records.data?.length ?? 0} gateway requests`}>
          <DataTable columns={reqColumns} data={recent} pageSize={10} dense initialSorting={[{ id: "timestamp", desc: true }]} />
        </Panel>
        <Panel title="Configuration">
          <KeyValueGrid
            columns={2}
            items={[
              { label: "Weight", value: b.weight },
              { label: "Upstream auth", value: bc?.upstream_auth ? "Configured" : "None" },
              { label: <Hint content="Effective = global timeouts merged with this backend's override.">Connect timeout</Hint>, value: timeouts ? formatSeconds(timeouts.connect) : "—" },
              { label: "Read timeout", value: timeouts ? formatSeconds(timeouts.read) : "—" },
              { label: "Stream read", value: timeouts ? (timeouts.stream_read ? formatSeconds(timeouts.stream_read) : "= read") : "—" },
              { label: "Override", value: bc?.timeout_override ? "Per-backend" : "Global" },
            ]}
          />
          <Button variant="link" size="sm" className="mt-3 px-0" asChild>
            <a href={b.url} target="_blank" rel="noreferrer noopener">
              Open upstream <ExternalLink data-icon="inline-end" />
            </a>
          </Button>
        </Panel>
      </div>
    </Page>
  )
}
