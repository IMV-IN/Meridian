import { Activity, ArrowRight, MessageSquareText, Server, Timer, TriangleAlert, Zap } from "lucide-react"
import { useMemo } from "react"
import { Link, useNavigate } from "react-router"

import { BarList, SegmentedBar, TimeAreaChart, TimeLineChart } from "@/components/charts"
import { Page, PageHeader, Panel, TagList } from "@/components/page"
import { Meter, Sparkline, StatCard } from "@/components/stat"
import { BACKEND_HEALTH_META, StatusBadge, httpStatusTone, type Tone } from "@/components/status-badge"
import { ErrorState, LoadingGrid } from "@/components/states"
import { TimeAgo } from "@/components/misc"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useConfig, useControl, useControlOverview, useMetrics, useRecentRequests, useStatus } from "@/hooks/queries"
import type { BackendStatus, RequestRecord } from "@/lib/api/types"
import { backendLabel, formatBytes, formatEwma, formatMs, formatMsShort, formatNumber, formatPercent, percentile, titleCase } from "@/lib/format"
import { sumBy } from "@/lib/prometheus"
import { backendHealth } from "@/lib/telemetry"
import { useTelemetry } from "@/providers/telemetry-provider"

function useKpis(records: RequestRecord[] | undefined) {
  const { traffic } = useTelemetry()
  return useMemo(() => {
    const recent = traffic.slice(-30)
    const last = recent[recent.length - 1]
    const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
    const rps = avg(recent.slice(-5).map((p) => p.rps))
    const okWindow = recent.reduce(
      (acc, p) => ({ ok: acc.ok + p.rps2xx, all: acc.all + p.rps }),
      { ok: 0, all: 0 },
    )
    // Fall back to the recent-request ring buffer before samples accumulate.
    const recs = records ?? []
    const success = okWindow.all > 0 ? okWindow.ok / okWindow.all : recs.length ? recs.filter((r) => r.status_code < 400).length / recs.length : null
    const lat = recs.map((r) => r.latency_ms).sort((a, b) => a - b)
    const p95 = last?.p95 ?? percentile(lat, 95)
    const p50 = last?.p50 ?? percentile(lat, 50)
    return {
      rps,
      success,
      p50,
      p95,
      rpsTrend: traffic.map((p) => p.rps),
      p95Trend: traffic.map((p) => p.p95),
      errTrend: traffic.map((p) => (p.errorRate === null ? 0 : p.errorRate * 100)),
    }
  }, [traffic, records])
}

function BackendRow({ b, share, trend }: { b: BackendStatus; share: number; trend: number[] }) {
  const navigate = useNavigate()
  const h = BACKEND_HEALTH_META[backendHealth(b)]
  const label = backendLabel(b.name)
  return (
    <TableRow className="cursor-pointer" onClick={() => navigate(`/backends/${encodeURIComponent(b.name)}`)}>
      <TableCell className="max-w-[200px] py-2.5">
        <div className="flex min-w-0 flex-col" title={b.name}>
          <span className="truncate font-medium">{label.primary}</span>
          <span className="truncate text-xs text-muted-foreground">
            {label.secondary ? `${label.secondary} · ` : `${b.engine} · `}
            {b.model || "any model"}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <StatusBadge tone={h.tone} size="sm">
          {h.label}
        </StatusBadge>
      </TableCell>
      <TableCell className="hidden 2xl:table-cell">
        <TagList tags={b.tags} max={2} className="flex-nowrap" />
      </TableCell>
      <TableCell className="text-right tabular">{b.inflight}</TableCell>
      <TableCell className="text-right tabular">{formatEwma(b.ewma_latency_ms)}</TableCell>
      <TableCell className="hidden w-28 md:table-cell">
        <Sparkline values={trend} height={24} />
      </TableCell>
      <TableCell className="w-32">
        <div className="flex items-center gap-2">
          <Meter value={share} warnAt={2} size="sm" label={`${b.name} traffic share`} />
          <span className="w-9 text-right text-xs tabular text-muted-foreground">{formatPercent(share, 0)}</span>
        </div>
      </TableCell>
    </TableRow>
  )
}

function FleetCard() {
  const { api } = useControl()
  const ov = useControlOverview()
  if (!api) {
    return (
      <Panel title="GPU fleet" description="meridian-control">
        <div className="space-y-3 text-sm text-muted-foreground">
          <p>Connect the control plane to see GPU hosts, engines and pending enrollments here.</p>
          <Button variant="outline" size="sm" asChild>
            <Link to="/settings?tab=control">Connect control plane</Link>
          </Button>
        </div>
      </Panel>
    )
  }
  if (ov.isError) return <Panel title="GPU fleet"><ErrorState error={ov.error} onRetry={() => ov.refetch()} className="py-6" /></Panel>
  const d = ov.data
  const online = d?.nodes.by_status.online ?? 0
  return (
    <Panel
      title="GPU fleet"
      description="meridian-control"
      actions={
        <Button variant="ghost" size="xs" asChild>
          <Link to="/fleet">
            Nodes <ArrowRight data-icon="inline-end" />
          </Link>
        </Button>
      }
    >
      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-xs text-muted-foreground">Nodes online</div>
          <div className="text-xl font-semibold">
            {d ? online : "—"}
            <span className="text-sm font-normal text-muted-foreground">/{d?.nodes.total ?? "—"}</span>
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Engines ready</div>
          <div className="text-xl font-semibold">
            {d?.engines.ready ?? "—"}
            <span className="text-sm font-normal text-muted-foreground">/{d?.engines.total ?? "—"}</span>
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Free VRAM</div>
          <div className="text-xl font-semibold">{d ? formatBytes(d.allocatable_vram_bytes, 0) : "—"}</div>
        </div>
      </div>
      {d && (d.claims_pending > 0 || d.engines.failed > 0) && (
        <div className="mt-4 space-y-2">
          {d.claims_pending > 0 && (
            <Link to="/fleet/enrollment" className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted/50">
              <StatusBadge tone="warning" size="sm">
                {d.claims_pending} pending approval
              </StatusBadge>
              <ArrowRight className="size-3.5 text-muted-foreground" />
            </Link>
          )}
          {d.engines.failed > 0 && (
            <Link to="/fleet" className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted/50">
              <StatusBadge tone="critical" size="sm">
                {d.engines.failed} engine{d.engines.failed === 1 ? "" : "s"} failed
              </StatusBadge>
              <ArrowRight className="size-3.5 text-muted-foreground" />
            </Link>
          )}
        </div>
      )}
    </Panel>
  )
}

function RolloutCard() {
  const status = useStatus()
  const config = useConfig()
  const records = useRecentRequests()
  const canary = status.data?.canary
  const cfg = config.data
  const tiers = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const r of records.data ?? []) if (r.tier) counts[r.tier] = (counts[r.tier] ?? 0) + 1
    return counts
  }, [records.data])
  const tierColors: Record<string, string> = {
    default: "var(--series-1)",
    long_prompt: "var(--series-2)",
    long_decode: "var(--series-3)",
  }
  let canaryTone: Tone = "info"
  let canaryLabel = "Rolling out"
  if (canary?.rolled_back) {
    canaryTone = "critical"
    canaryLabel = "Rolled back"
  } else if (canary?.schedule_done) {
    canaryTone = "good"
    canaryLabel = "Holding"
  }
  return (
    <Panel
      title="Routing"
      description={cfg ? `Strategy · ${titleCase(cfg.gateway.strategy)}` : undefined}
      actions={
        <Button variant="ghost" size="xs" asChild>
          <Link to="/routing">
            Details <ArrowRight data-icon="inline-end" />
          </Link>
        </Button>
      }
    >
      <div className="space-y-5">
        {canary ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Canary rollout</span>
              <StatusBadge tone={canaryTone} size="sm">
                {canaryLabel}
              </StatusBadge>
            </div>
            <Meter value={canary.weight / 100} warnAt={2} label="canary weight" />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                <span className="font-medium text-foreground tabular">{canary.weight}%</span> to{" "}
                <TagList tags={canary.canary_tags} />
              </span>
              <span>
                errors {canary.window_error_rate === null ? "—" : formatPercent(canary.window_error_rate)} ·{" "}
                {canary.window_samples} samples
              </span>
            </div>
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">No canary rollout configured.</div>
        )}
        {cfg?.tiering.enabled && (
          <div className="space-y-2">
            <span className="text-sm font-medium">Workload tiers (recent)</span>
            <SegmentedBar
              parts={["default", "long_prompt", "long_decode"].map((k) => ({
                key: k,
                label: titleCase(k),
                value: tiers[k] ?? 0,
                color: tierColors[k],
              }))}
            />
          </div>
        )}
        {cfg?.session_affinity.enabled && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Pinned sessions</span>
            <span className="font-medium tabular">{formatNumber(cfg.session_affinity.active_sessions ?? 0)}</span>
          </div>
        )}
      </div>
    </Panel>
  )
}

export default function OverviewPage() {
  const status = useStatus()
  const records = useRecentRequests()
  const metrics = useMetrics()
  const { traffic, backends: backendSeries, since } = useTelemetry()
  const kpi = useKpis(records.data)

  const backends = status.data?.backends ?? []
  const healthy = backends.filter((b) => backendHealth(b) === "healthy").length
  const inflight = backends.reduce((a, b) => a + b.inflight, 0)
  const unhealthy = backends.filter((b) => ["unhealthy", "circuit_open"].includes(backendHealth(b)))

  const share = useMemo(() => {
    const window = traffic.slice(-15)
    const totals: Record<string, number> = {}
    for (const p of window) for (const [k, v] of Object.entries(p.byBackend)) totals[k] = (totals[k] ?? 0) + v
    const sum = Object.values(totals).reduce((a, b) => a + b, 0)
    return Object.fromEntries(Object.entries(totals).map(([k, v]) => [k, sum ? v / sum : 0]))
  }, [traffic])

  const byModel = useMemo(() => {
    if (!metrics.parsed) return []
    return Object.entries(sumBy(metrics.parsed, "meridian_requests_total", "model"))
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
  }, [metrics.parsed])

  const errors = (records.data ?? []).filter((r) => r.status_code >= 400).slice(0, 6)

  if (status.isError && !status.data) {
    return (
      <Page>
        <PageHeader title="Overview" />
        <ErrorState error={status.error} onRetry={() => status.refetch()} />
      </Page>
    )
  }

  return (
    <Page>
      <PageHeader
        title="Overview"
        description={
          <>
            Live health and traffic for this gateway.{" "}
            {since ? (
              <>
                Sampling since <TimeAgo value={since} />.
              </>
            ) : (
              "Sampling starts now."
            )}
          </>
        }
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link to="/requests">
                <Activity data-icon="inline-start" /> Request log
              </Link>
            </Button>
            <Button size="sm" asChild>
              <Link to="/playground">
                <MessageSquareText data-icon="inline-start" /> Playground
              </Link>
            </Button>
          </>
        }
      />

      {unhealthy.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[color-mix(in_oklab,var(--status-critical)_35%,transparent)] bg-[color-mix(in_oklab,var(--status-critical)_7%,transparent)] px-4 py-3">
          <TriangleAlert className="size-4 text-(--status-critical)" />
          <span className="text-sm font-medium">
            {unhealthy.length} backend{unhealthy.length === 1 ? " is" : "s are"} out of rotation
          </span>
          <span className="flex flex-wrap gap-1.5">
            {unhealthy.map((b) => (
              <Link key={b.name} to={`/backends/${encodeURIComponent(b.name)}`} className="text-sm underline underline-offset-2">
                {b.name}
              </Link>
            ))}
          </span>
        </div>
      )}

      {!status.data ? (
        <LoadingGrid count={4} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
          <StatCard
            label="Throughput"
            icon={Zap}
            value={kpi.rps === null ? "—" : formatNumber(kpi.rps, { digits: kpi.rps < 10 ? 1 : 0 })}
            unit="req/s"
            hint={`${formatNumber(inflight)} in flight now`}
            trend={kpi.rpsTrend}
            info="Requests per second across all backends, derived from meridian_requests_total between refreshes."
          />
          <StatCard
            label="Success rate"
            icon={Activity}
            value={formatPercent(kpi.success)}
            tone={kpi.success === null ? "neutral" : kpi.success >= 0.99 ? "good" : kpi.success >= 0.95 ? "warning" : "critical"}
            hint="non-error responses, last minute"
            trend={kpi.errTrend}
            trendColor={kpi.errTrend.some((v) => (v ?? 0) > 0) ? "var(--status-critical)" : "var(--muted-foreground)"}
            info="Share of 2xx responses. The trend line shows the 5xx error rate (%)."
          />
          <StatCard
            label="Latency p95"
            icon={Timer}
            value={formatMs(kpi.p95)}
            hint={`p50 ${formatMs(kpi.p50)}`}
            trend={kpi.p95Trend}
            trendColor="var(--series-2)"
            info="Windowed estimate from meridian_request_latency_ms histogram deltas (Prometheus histogram_quantile semantics)."
          />
          <StatCard
            label="Backends in rotation"
            icon={Server}
            value={
              <>
                {healthy}
                <span className="text-base font-normal text-muted-foreground">/{backends.length}</span>
              </>
            }
            tone={backends.length === 0 ? "warning" : healthy === backends.length ? "good" : healthy === 0 ? "critical" : "warning"}
            hint={`Strategy · ${titleCase(status.data.strategy)}`}
          />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Traffic"
          description="Requests per second by response class"
          actions={<span className="text-xs text-muted-foreground tabular">{traffic.length} samples</span>}
        >
          <TimeAreaChart
            data={traffic}
            stacked
            height={240}
            valueFormatter={(v) => formatNumber(v, { digits: v < 10 ? 1 : 0 })}
            series={[
              { key: "rps2xx", label: "2xx", color: "var(--status-good)" },
              { key: "rps4xx", label: "4xx", color: "var(--status-warning)" },
              { key: "rps5xx", label: "5xx", color: "var(--status-critical)" },
            ]}
          />
        </Panel>
        <Panel title="Latency" description="Windowed percentiles">
          <TimeLineChart
            data={traffic}
            height={240}
            valueFormatter={(v) => formatMs(v)}
            axisFormatter={formatMsShort}
            series={[
              { key: "p50", label: "p50", color: "var(--series-1)" },
              { key: "p95", label: "p95", color: "var(--series-2)" },
            ]}
          />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Backends"
          description="Health, load and EWMA latency trend · share of the last ~30s of traffic"
          actions={
            <Button variant="ghost" size="xs" asChild>
              <Link to="/backends">
                All backends <ArrowRight data-icon="inline-end" />
              </Link>
            </Button>
          }
          contentClassName="px-0"
        >
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4 text-xs">Backend</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="hidden text-xs 2xl:table-cell">Tags</TableHead>
                <TableHead className="text-right text-xs">In flight</TableHead>
                <TableHead className="text-right text-xs">EWMA</TableHead>
                <TableHead className="hidden text-xs md:table-cell">Latency trend</TableHead>
                <TableHead className="pr-4 text-xs">Share</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className="[&_td:first-child]:pl-4 [&_td:last-child]:pr-4">
              {backends.map((b) => (
                <BackendRow
                  key={b.name}
                  b={b}
                  share={share[b.name] ?? 0}
                  trend={(backendSeries[b.name] ?? []).map((p) => p.ewma)}
                />
              ))}
              {backends.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                    No backends registered. Add <code>backends:</code> to the gateway config.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Panel>
        <div className="grid gap-4">
          <RolloutCard />
          <FleetCard />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Recent errors"
          description="Latest non-2xx responses from the request ring buffer"
          actions={
            <Button variant="ghost" size="xs" asChild>
              <Link to="/requests?status=errors">
                View all <ArrowRight data-icon="inline-end" />
              </Link>
            </Button>
          }
        >
          {errors.length === 0 ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <StatusBadge tone="good" size="sm">
                Clear
              </StatusBadge>
              No errors in the last {records.data?.length ?? 0} requests.
            </div>
          ) : (
            <ul className="divide-y">
              {errors.map((r) => (
                <li key={r.request_id}>
                  <Link
                    to={`/requests?q=${encodeURIComponent(r.request_id)}`}
                    className="flex items-center gap-3 py-2 text-sm hover:bg-muted/40"
                  >
                    <StatusBadge tone={httpStatusTone(r.status_code)} size="sm" className="w-14 justify-center">
                      {r.status_code}
                    </StatusBadge>
                    <span className="min-w-0 flex-1 truncate">
                      <span className="font-medium">{r.error_type ?? "error"}</span>
                      <span className="text-muted-foreground">
                        {" "}
                        · {r.chosen_backend} · {r.org_id ?? "anonymous"}
                      </span>
                    </span>
                    <span className="hidden font-mono text-xs text-muted-foreground sm:inline">{r.request_id}</span>
                    <span className="w-20 text-right text-xs text-muted-foreground">
                      <TimeAgo value={r.timestamp} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Requests by model" description="Since gateway start (Prometheus)">
          <BarList
            items={byModel.map(([m, n]) => ({ key: m, label: <span className="font-mono text-xs">{m}</span>, value: n }))}
          />
          <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
            <span>Total since start</span>
            <span className="font-mono tabular text-foreground">
              {formatNumber(byModel.reduce((a, [, n]) => a + n, 0))} requests
            </span>
          </div>
        </Panel>
      </div>
    </Page>
  )
}
