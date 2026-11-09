import { Ban, Coins, Gauge, RotateCcw, ShieldAlert, Timer, Zap } from "lucide-react"
import { useMemo } from "react"

import { BarList, ColumnChart } from "@/components/charts"
import { CodeBlock } from "@/components/code"
import { SearchInput } from "@/components/misc"
import { Page, PageHeader, Panel } from "@/components/page"
import { StatCard } from "@/components/stat"
import { ErrorState, LoadingGrid } from "@/components/states"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useMetrics } from "@/hooks/queries"
import { gatewayOrigin } from "@/lib/api/gateway"
import { backendLabel, formatMs, formatNumber, formatPercent, titleCase } from "@/lib/format"
import { bucketCounts, histogram, quantile, select, sum, sumBy, type Exposition } from "@/lib/prometheus"
import { cn } from "@/lib/utils"
import { useUrlState } from "@/hooks/use-url-state"

function useDerived(exp: Exposition | null) {
  return useMemo(() => {
    if (!exp) return null
    const backends = Object.keys(sumBy(exp, "meridian_requests_total", "backend")).sort()
    const perBackend = backends.map((b) => {
      const byStatus = sumBy(exp, "meridian_requests_total", "status", { backend: b })
      let c2 = 0
      let c4 = 0
      let c5 = 0
      for (const [s, n] of Object.entries(byStatus)) {
        const code = Number(s)
        if (code >= 500) c5 += n
        else if (code >= 400) c4 += n
        else c2 += n
      }
      const h = histogram(exp, "meridian_request_latency_ms", { backend: b })
      return {
        backend: b,
        total: c2 + c4 + c5,
        c2,
        c4,
        c5,
        p50: quantile(0.5, h),
        p90: quantile(0.9, h),
        p99: quantile(0.99, h),
        mean: h.count ? h.sum / h.count : null,
        retries: sum(exp, "meridian_upstream_retries_total", { backend: b }),
      }
    })
    const all = histogram(exp, "meridian_request_latency_ms")
    return {
      perBackend,
      total: sum(exp, "meridian_requests_total"),
      streamShare: (() => {
        const t = sum(exp, "meridian_requests_total")
        return t ? sum(exp, "meridian_requests_total", { stream: "true" }) / t : null
      })(),
      tokensByModel: sumBy(exp, "meridian_tokens_total", "model"),
      tokensByKind: sumBy(exp, "meridian_tokens_total", "kind"),
      tokens: sum(exp, "meridian_tokens_total"),
      retries: sum(exp, "meridian_upstream_retries_total"),
      rollbacks: sum(exp, "meridian_canary_rollbacks_total"),
      canaryWeight: select(exp, "meridian_canary_weight")[0]?.value ?? null,
      budget: select(exp, "meridian_budget_rejections_total").map((s) => ({ key: `${s.labels.level}/${s.labels.period}`, value: s.value })),
      ratelimit: sumBy(exp, "meridian_ratelimit_rejections_total", "scope"),
      pii: select(exp, "meridian_pii_detections_total").map((s) => ({ key: `${s.labels.entity} · ${s.labels.policy}`, value: s.value })),
      reconciles: sumBy(exp, "meridian_budget_reconciles_total", "direction"),
      cacheDecisions: sumBy(exp, "meridian_cache_affinity_decisions_total", "outcome"),
      cacheUnavailable: sum(exp, "meridian_cache_feedback_unavailable_total"),
      prefixEntries: select(exp, "meridian_prefix_index_entries")[0]?.value ?? null,
      overall: all,
      p50: quantile(0.5, all),
      p95: quantile(0.95, all),
      dist: bucketCounts(all).map((x) => ({ bucket: x.label, count: x.count })),
    }
  }, [exp])
}

function RawExplorer({ exp, text }: { exp: Exposition; text: string }) {
  const [q, setQ] = useUrlState("q")
  const [metric, setMetric] = useUrlState("metric")
  const selected = metric || null
  const setSelected = (v: string | null) => setMetric(v ?? "")
  const families = useMemo(() => {
    const names = new Map<string, { type: string; help: string; count: number }>()
    for (const [name, f] of exp.families) {
      if (name.endsWith("_created")) continue
      names.set(name, { type: f.type, help: f.help, count: 0 })
    }
    for (const s of exp.samples) {
      const base = [...names.keys()].find((n) => s.name === n || s.name.startsWith(`${n}_`)) ?? s.name
      const entry = names.get(base)
      if (entry) entry.count += 1
    }
    return [...names.entries()]
      .filter(([n, f]) => !q || n.includes(q.toLowerCase()) || f.help.toLowerCase().includes(q.toLowerCase()))
      .sort((a, b) => a[0].localeCompare(b[0]))
  }, [exp, q])
  const current = selected ?? families[0]?.[0] ?? null
  const samples = current ? exp.samples.filter((s) => s.name === current || s.name.startsWith(`${current}_`)) : []
  const labelKeys = [...new Set(samples.flatMap((s) => Object.keys(s.labels)))]

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Panel title="Metric families" contentClassName="space-y-3">
        <SearchInput value={q} onChange={setQ} placeholder="Filter metrics…" className="sm:w-full" />
        <ul className="scrollbar-thin max-h-[560px] space-y-0.5 overflow-y-auto">
          {families.map(([name, f]) => (
            <li key={name}>
              <button
                type="button"
                onClick={() => setSelected(name)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted",
                  current === name && "bg-muted",
                )}
              >
                <span className="min-w-0 truncate font-mono text-xs">{name}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <Badge variant="outline" className="h-4 px-1 text-[10px]">
                    {f.type}
                  </Badge>
                  <span className="text-[11px] text-muted-foreground tabular">{f.count}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      <div className="flex min-w-0 flex-col gap-4">
        {current && (
          <Panel title={<span className="font-mono">{current}</span>} description={exp.families.get(current)?.help} contentClassName="px-0">
            {samples.length === 0 ? (
              <p className="px-4 text-sm text-muted-foreground">No samples yet — this series appears after its first observation.</p>
            ) : (
              <div className="scrollbar-thin max-h-[420px] overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-4 text-xs">Series</TableHead>
                      {labelKeys.map((k) => (
                        <TableHead key={k} className="text-xs">
                          {k}
                        </TableHead>
                      ))}
                      <TableHead className="pr-4 text-right text-xs">Value</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {samples.map((s, i) => (
                      <TableRow key={i}>
                        <TableCell className="pl-4 font-mono text-xs text-muted-foreground">{s.name.slice(current.length) || "·"}</TableCell>
                        {labelKeys.map((k) => (
                          <TableCell key={k} className="font-mono text-xs">
                            {s.labels[k] ?? ""}
                          </TableCell>
                        ))}
                        <TableCell className="pr-4 text-right font-mono text-xs tabular">{formatNumber(s.value, { compact: false })}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Panel>
        )}
        <Panel title="Raw exposition" description="Exactly what Prometheus scrapes from /metrics">
          <CodeBlock code={text.split("\n").filter((l) => !l.includes("_created")).join("\n")} maxHeight={320} />
        </Panel>
      </div>
    </div>
  )
}

export default function MetricsPage() {
  const [tab, setTab] = useUrlState("tab", "dashboards")
  const m = useMetrics()
  const d = useDerived(m.parsed)

  if (m.isError && !m.data) return <Page><ErrorState error={m.error} onRetry={() => m.refetch()} /></Page>

  return (
    <Page>
      <PageHeader
        title="Observability"
        description="Cumulative Prometheus counters since the gateway process started. Labels are cardinality-safe: backend, model, status — never tenant, user or request IDs."
      />
      <Tabs value={tab} onValueChange={setTab} className="gap-4">
        <TabsList>
          <TabsTrigger value="dashboards">Dashboards</TabsTrigger>
          <TabsTrigger value="explorer">Metric explorer</TabsTrigger>
          <TabsTrigger value="scrape">Scrape &amp; Grafana</TabsTrigger>
        </TabsList>
        <TabsContent value="dashboards" className="flex flex-col gap-4">
          {!d ? (
            <LoadingGrid count={4} />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
                <StatCard label="Requests proxied" icon={Zap} value={formatNumber(d.total)} hint={`${formatPercent(d.streamShare, 0)} streaming`} />
                <StatCard label="Latency p95" icon={Timer} value={formatMs(d.p95)} hint={`p50 ${formatMs(d.p50)} · all backends`} />
                <StatCard label="Tokens attributed" icon={Coins} value={formatNumber(d.tokens)} hint={`${formatNumber(d.tokensByKind.prompt ?? 0)} prompt · ${formatNumber(d.tokensByKind.completion ?? 0)} completion`} />
                <StatCard
                  label="Upstream retries"
                  icon={RotateCcw}
                  value={formatNumber(d.retries)}
                  hint={`${formatNumber(d.rollbacks)} canary rollback${d.rollbacks === 1 ? "" : "s"}`}
                  tone={d.rollbacks > 0 ? "warning" : "neutral"}
                />
              </div>

              <Panel title="Per-backend service levels" description="Latency quantiles estimated from histogram buckets (histogram_quantile semantics)" contentClassName="px-0">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="pl-4 text-xs">Backend</TableHead>
                      <TableHead className="text-right text-xs">Requests</TableHead>
                      <TableHead className="text-right text-xs">4xx</TableHead>
                      <TableHead className="text-right text-xs">5xx</TableHead>
                      <TableHead className="text-right text-xs">Error rate</TableHead>
                      <TableHead className="text-right text-xs">Mean</TableHead>
                      <TableHead className="text-right text-xs">p50</TableHead>
                      <TableHead className="text-right text-xs">p90</TableHead>
                      <TableHead className="text-right text-xs">p99</TableHead>
                      <TableHead className="pr-4 text-right text-xs">Retries</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody className="tabular">
                    {d.perBackend.map((r) => {
                      const err = r.total ? r.c5 / r.total : 0
                      return (
                        <TableRow key={r.backend}>
                          <TableCell className="max-w-56 truncate pl-4 font-medium" title={r.backend}>
                            {backendLabel(r.backend).primary}
                          </TableCell>
                          <TableCell className="text-right">{formatNumber(r.total)}</TableCell>
                          <TableCell className="text-right">{formatNumber(r.c4)}</TableCell>
                          <TableCell className="text-right">{formatNumber(r.c5)}</TableCell>
                          <TableCell className="text-right">
                            <span className={cn(err > 0.05 && "font-semibold text-critical-ink", err > 0.01 && err <= 0.05 && "font-medium")}>{formatPercent(err)}</span>
                          </TableCell>
                          <TableCell className="text-right">{formatMs(r.mean)}</TableCell>
                          <TableCell className="text-right">{formatMs(r.p50)}</TableCell>
                          <TableCell className="text-right">{formatMs(r.p90)}</TableCell>
                          <TableCell className="text-right">{formatMs(r.p99)}</TableCell>
                          <TableCell className="pr-4 text-right">{formatNumber(r.retries)}</TableCell>
                        </TableRow>
                      )
                    })}
                    {d.perBackend.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={10} className="py-8 text-center text-sm text-muted-foreground">
                          No requests observed yet.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </Panel>

              <div className="grid gap-4 xl:grid-cols-3">
                <Panel className="xl:col-span-2" title="Latency distribution" description="All backends, per histogram bucket (ms)">
                  <ColumnChart data={d.dist} xKey="bucket" yKey="count" label="Requests" height={230} />
                </Panel>
                <Panel title="Tokens by model" description="Actual usage scraped from backend responses">
                  <BarList items={Object.entries(d.tokensByModel).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ key: k, label: <span className="font-mono text-xs">{k}</span>, value: v }))} />
                </Panel>
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <Panel title={<span className="flex items-center gap-2"><Ban className="size-4" /> Rate-limit rejections</span>} description="By bucket scope">
                  <BarList
                    color="var(--status-warning)"
                    items={Object.entries(d.ratelimit).map(([k, v]) => ({ key: k, label: titleCase(k), value: v }))}
                  />
                </Panel>
                <Panel title={<span className="flex items-center gap-2"><Gauge className="size-4" /> Budget rejections</span>} description="By cascade level / period">
                  <BarList color="var(--status-serious)" items={d.budget.map((b) => ({ key: b.key, label: b.key, value: b.value }))} />
                  {Object.keys(d.reconciles).length > 0 && (
                    <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
                      Reconciliations: {Object.entries(d.reconciles).map(([k, v]) => `${k} ${formatNumber(v)}`).join(" · ")}
                    </p>
                  )}
                </Panel>
                <Panel title={<span className="flex items-center gap-2"><ShieldAlert className="size-4" /> PII detections</span>} description="Entity · policy applied (values never logged)">
                  <BarList items={d.pii.map((p) => ({ key: p.key, label: p.key, value: p.value }))} />
                </Panel>
              </div>

              {(Object.keys(d.cacheDecisions).length > 0 || !!d.prefixEntries) && (
                <Panel title="Cache-affinity routing (experimental)" description="Prediction outcomes corrected by engine-reported cached tokens">
                  <div className="grid gap-6 md:grid-cols-[1fr_220px]">
                    <BarList items={Object.entries(d.cacheDecisions).map(([k, v]) => ({ key: k, label: titleCase(k), value: v }))} />
                    <div className="space-y-3 text-sm">
                      <div>
                        <div className="text-xs text-muted-foreground">Prefix index entries</div>
                        <div className="text-xl font-semibold">{formatNumber(d.prefixEntries)}</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">Feedback unavailable</div>
                        <div className="text-xl font-semibold">{formatNumber(d.cacheUnavailable)}</div>
                      </div>
                    </div>
                  </div>
                </Panel>
              )}
            </>
          )}
        </TabsContent>
        <TabsContent value="explorer">{m.parsed && m.data ? <RawExplorer exp={m.parsed} text={m.data} /> : <LoadingGrid count={2} />}</TabsContent>
        <TabsContent value="scrape" className="grid gap-4 lg:grid-cols-2">
          <Panel title="Prometheus scrape config" description="/metrics is unauthenticated by design — restrict it at the network layer.">
            <CodeBlock
              language="yaml"
              code={`scrape_configs:
  - job_name: meridian
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ["${new URL(gatewayOrigin()).host}"]`}
            />
          </Panel>
          <Panel title="Grafana dashboards" description="Provisioned with the Helm chart, or import the JSON directly.">
            <ul className="space-y-2 text-sm">
              <li>
                <code className="text-xs">deploy/grafana/dashboard-overview.json</code> — traffic, latency, backend health, circuit and idle state
              </li>
              <li>
                <code className="text-xs">deploy/grafana/dashboard-governance.json</code> — budgets, rate limits, PII, tokens and canary
              </li>
            </ul>
            <CodeBlock
              className="mt-4"
              language="promql"
              code={`# p95 latency by backend (5m)
histogram_quantile(0.95, sum by (le, backend) (rate(meridian_request_latency_ms_bucket[5m])))

# 5xx ratio
sum(rate(meridian_requests_total{status=~"5.."}[5m])) / sum(rate(meridian_requests_total[5m]))`}
            />
          </Panel>
        </TabsContent>
      </Tabs>
    </Page>
  )
}
