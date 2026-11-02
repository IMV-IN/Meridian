import { Coins, Download, Hash, Info, Sigma } from "lucide-react"
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { SERIES, StackedColumnChart } from "@/components/charts"
import { Page, PageHeader, Panel } from "@/components/page"
import { Meter, StatCard } from "@/components/stat"
import { ErrorState, FeatureOffState, LoadingGrid } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useConfig, useUsage } from "@/hooks/queries"
import { gateway } from "@/lib/api/gateway"
import type { UsageQuery, UsageRow } from "@/lib/api/types"
import { formatCurrency, formatNumber, formatPercent } from "@/lib/format"
import { useAuth } from "@/providers/auth-provider"

const WINDOWS = [7, 30, 90] as const

function dayRange(days: number): string[] {
  const out: string[] = []
  const now = new Date()
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - i))
    out.push(d.toISOString().slice(0, 10))
  }
  return out
}

const shortDay = (d: string) => {
  const dt = new Date(`${d}T00:00:00Z`)
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(dt)
}

type Dim = "org_id" | "team_id" | "key_id" | "model"

function Breakdown({ rows, dim, currency }: { rows: UsageRow[]; dim: Dim; currency: string }) {
  const agg = useMemo(() => {
    const m = new Map<string, { requests: number; prompt: number; completion: number; cost: number }>()
    for (const r of rows) {
      const k = (dim === "team_id" ? `${r.org_id} / ${r.team_id || "—"}` : r[dim]) || "—"
      const cur = m.get(k) ?? { requests: 0, prompt: 0, completion: 0, cost: 0 }
      cur.requests += r.requests
      cur.prompt += r.prompt_tokens
      cur.completion += r.completion_tokens
      cur.cost += r.cost
      m.set(k, cur)
    }
    const total = [...m.values()].reduce((a, v) => a + v.cost, 0)
    const totalTokens = [...m.values()].reduce((a, v) => a + v.prompt + v.completion, 0)
    return [...m.entries()]
      .map(([k, v]) => ({ k, ...v, share: total > 0 ? v.cost / total : totalTokens ? (v.prompt + v.completion) / totalTokens : 0 }))
      .sort((a, b) => b.cost - a.cost || b.prompt + b.completion - (a.prompt + a.completion))
  }, [rows, dim])
  const label = { org_id: "Org", team_id: "Org / team", key_id: "API key", model: "Model" }[dim]
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-4 text-xs">{label}</TableHead>
          <TableHead className="text-right text-xs">Requests</TableHead>
          <TableHead className="text-right text-xs">Prompt tokens</TableHead>
          <TableHead className="text-right text-xs">Completion tokens</TableHead>
          <TableHead className="text-right text-xs">Cost</TableHead>
          <TableHead className="w-44 pr-4 text-xs">Share</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody className="tabular">
        {agg.map((r) => (
          <TableRow key={r.k}>
            <TableCell className={dim === "key_id" || dim === "model" ? "pl-4 font-mono text-xs" : "pl-4 font-medium"}>
              {r.k === "" || r.k === "—" ? <span className="text-muted-foreground">unattributed</span> : r.k}
            </TableCell>
            <TableCell className="text-right">{formatNumber(r.requests)}</TableCell>
            <TableCell className="text-right">{formatNumber(r.prompt)}</TableCell>
            <TableCell className="text-right">{formatNumber(r.completion)}</TableCell>
            <TableCell className="text-right font-medium">{formatCurrency(r.cost, currency)}</TableCell>
            <TableCell className="pr-4">
              <div className="flex items-center gap-2">
                <Meter value={r.share} warnAt={2} size="sm" label={`${r.k} share`} />
                <span className="w-10 text-right text-xs text-muted-foreground">{formatPercent(r.share, 0)}</span>
              </div>
            </TableCell>
          </TableRow>
        ))}
        {agg.length === 0 && (
          <TableRow>
            <TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">
              No usage recorded in this window.
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}

export default function UsagePage() {
  const { can, identity, features, authEnabled } = useAuth()
  const config = useConfig()
  const [params, setParams] = useSearchParams()
  const [exporting, setExporting] = useState(false)
  const windowDays = Number(params.get("window") ?? 30)
  const admin = can("read_all_cost")
  const org = admin ? (params.get("org") ?? "") : (identity?.org_id ?? "")
  const team = params.get("team") ?? ""
  const key = params.get("key") ?? ""

  const query: UsageQuery = { window_days: windowDays, org: org || undefined, team: team || undefined, key: key || undefined }
  const usage = useUsage(query, features.cost)
  // Unfiltered (but still server-scoped) fetch to populate filter options.
  const options = useUsage({ window_days: windowDays }, features.cost)

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (!v || v === "all") next.delete(k)
    else next.set(k, v)
    if (k === "org") next.delete("team")
    setParams(next, { replace: true })
  }

  const rows = useMemo(() => usage.data?.rows ?? [], [usage.data])
  const currency = usage.data?.currency ?? config.data?.cost.currency ?? "USD"
  const opt = options.data?.rows ?? []
  const orgs = [...new Set(opt.map((r) => r.org_id))].sort()
  const teams = [...new Set(opt.filter((r) => !org || r.org_id === org).map((r) => r.team_id).filter(Boolean))].sort()
  const keys = [...new Set(opt.filter((r) => !org || r.org_id === org).map((r) => r.key_id).filter(Boolean))].sort()

  const totals = useMemo(
    () =>
      rows.reduce(
        (a, r) => ({
          cost: a.cost + r.cost,
          prompt: a.prompt + r.prompt_tokens,
          completion: a.completion + r.completion_tokens,
          requests: a.requests + r.requests,
        }),
        { cost: 0, prompt: 0, completion: 0, requests: 0 },
      ),
    [rows],
  )

  const charts = useMemo(() => {
    const days = dayRange(windowDays)
    const modelTotals = new Map<string, number>()
    for (const r of rows) modelTotals.set(r.model, (modelTotals.get(r.model) ?? 0) + r.cost + (r.prompt_tokens + r.completion_tokens) * 1e-9)
    const ranked = [...modelTotals.entries()].sort((a, b) => b[1] - a[1]).map(([m]) => m)
    const top = ranked.slice(0, 7)
    const hasOther = ranked.length > 7
    const keyOf = (m: string) => (top.includes(m) ? `m${top.indexOf(m)}` : "other")
    const costByDay = days.map((d) => {
      const row: Record<string, string | number> = { day: d }
      for (let i = 0; i < top.length; i++) row[`m${i}`] = 0
      if (hasOther) row.other = 0
      return row
    })
    const tokByDay = days.map((d) => ({ day: d, prompt: 0, completion: 0 }) as Record<string, string | number>)
    const idx = new Map(days.map((d, i) => [d, i]))
    for (const r of rows) {
      const i = idx.get(r.day)
      if (i === undefined) continue
      const k = keyOf(r.model)
      costByDay[i][k] = Number(costByDay[i][k] ?? 0) + r.cost
      tokByDay[i].prompt = Number(tokByDay[i].prompt) + r.prompt_tokens
      tokByDay[i].completion = Number(tokByDay[i].completion) + r.completion_tokens
    }
    const series = [
      ...top.map((m, i) => ({ key: `m${i}`, label: m, color: SERIES[i] })),
      ...(hasOther ? [{ key: "other", label: "Other", color: "var(--muted-foreground)" }] : []),
    ]
    return { costByDay, tokByDay, series }
  }, [rows, windowDays])

  const exportCsv = async () => {
    setExporting(true)
    try {
      await gateway.downloadUsageCsv(query)
      toast.success("Usage exported", { description: `${rows.length} rows · last ${windowDays} days` })
    } catch (e) {
      toast.error("Export failed", { description: e instanceof Error ? e.message : String(e) })
    } finally {
      setExporting(false)
    }
  }

  if (!features.cost)
    return (
      <Page>
        <PageHeader title="Usage & cost" />
        <FeatureOffState
          icon={Coins}
          title="Cost attribution is not enabled"
          description={
            authEnabled
              ? "Turn on the cost ledger to attribute actual backend token usage to orgs, teams and keys."
              : "Cost attribution requires API-key auth (usage must never be exported anonymously). Enable auth first, then the ledger."
          }
          snippet={`auth:\n  enabled: true\ncost:\n  enabled: true\n  store: sqlite\n  currency: USD\n  models:\n    my-model: { prompt_per_1m: 0.20, completion_per_1m: 0.80 }`}
        />
      </Page>
    )

  return (
    <Page>
      <PageHeader
        title="Usage & cost"
        description={
          admin
            ? "Actual tokens reported by backends, priced per model. You can see every org."
            : `Actual tokens reported by backends for ${identity?.org_id}${identity?.team_id ? ` / ${identity.team_id}` : ""}. Other orgs are not visible to your key.`
        }
        actions={
          <Button variant="outline" size="sm" onClick={() => void exportCsv()} disabled={exporting || !rows.length}>
            {exporting ? <Spinner data-icon="inline-start" /> : <Download data-icon="inline-start" />}
            Export CSV
          </Button>
        }
      />

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <ToggleGroup type="single" variant="outline" size="sm" value={String(windowDays)} onValueChange={(v) => v && setParam("window", v === "30" ? "" : v)}>
          {WINDOWS.map((w) => (
            <ToggleGroupItem key={w} value={String(w)} className="px-3">
              {w}d
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {admin ? (
          <Select value={org || "all"} onValueChange={(v) => setParam("org", v)}>
            <SelectTrigger size="sm" className="w-full sm:w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All orgs</SelectItem>
              {orgs.map((o) => (
                <SelectItem key={o} value={o}>
                  {o}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Badge variant="secondary" className="h-8 px-3">
            org · {identity?.org_id}
          </Badge>
        )}
        {(!identity?.team_id || admin) && (
          <Select value={team || "all"} onValueChange={(v) => setParam("team", v)}>
            <SelectTrigger size="sm" className="w-full sm:w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All teams</SelectItem>
              {teams.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select value={key || "all"} onValueChange={(v) => setParam("key", v)}>
          <SelectTrigger size="sm" className="w-full sm:w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All API keys</SelectItem>
            {[...new Set([...keys, ...(key ? [key] : [])])].map((k) => (
              <SelectItem key={k} value={k}>
                <span className="font-mono text-xs">{k}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {(org && admin) || team || key ? (
          <Button variant="ghost" size="sm" onClick={() => setParams(windowDays === 30 ? new URLSearchParams() : new URLSearchParams({ window: String(windowDays) }), { replace: true })}>
            Reset
          </Button>
        ) : null}
        {usage.isFetching && <Spinner className="text-muted-foreground" />}
      </div>

      {usage.isError && !usage.data ? (
        <ErrorState error={usage.error} onRetry={() => usage.refetch()} />
      ) : !usage.data ? (
        <LoadingGrid count={4} />
      ) : (
        <div className={usage.isPlaceholderData ? "flex flex-col gap-4 opacity-70 transition-opacity" : "flex flex-col gap-4"}>
          <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
            <StatCard label={`Cost · ${windowDays}d`} icon={Coins} value={formatCurrency(totals.cost, currency)} hint={currency} />
            <StatCard label="Tokens" icon={Sigma} value={formatNumber(totals.prompt + totals.completion)} hint={`${formatNumber(totals.prompt)} prompt · ${formatNumber(totals.completion)} completion`} />
            <StatCard label="Requests" icon={Hash} value={formatNumber(totals.requests)} hint={totals.requests ? `${formatNumber((totals.prompt + totals.completion) / totals.requests, { digits: 0 })} tokens / request` : undefined} />
            <StatCard
              label="Blended price"
              value={totals.prompt + totals.completion ? formatCurrency((totals.cost / (totals.prompt + totals.completion)) * 1_000_000, currency) : "—"}
              hint="per 1M tokens"
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Daily cost by model" description={`UTC days · ${currency}`}>
              <StackedColumnChart data={charts.costByDay} xKey="day" series={charts.series} xFormatter={shortDay} valueFormatter={(v) => formatCurrency(v, currency)} height={260} />
            </Panel>
            <Panel title="Daily tokens" description="Prompt vs completion">
              <StackedColumnChart
                data={charts.tokByDay}
                xKey="day"
                xFormatter={shortDay}
                height={260}
                series={[
                  { key: "prompt", label: "Prompt", color: "var(--series-1)" },
                  { key: "completion", label: "Completion", color: "var(--series-2)" },
                ]}
              />
            </Panel>
          </div>

          <Panel title="Breakdown" contentClassName="px-0">
            <Tabs defaultValue={admin && !org ? "org_id" : "team_id"} className="gap-3">
              <TabsList className="mx-4">
                {admin && <TabsTrigger value="org_id">By org</TabsTrigger>}
                <TabsTrigger value="team_id">By team</TabsTrigger>
                <TabsTrigger value="key_id">By API key</TabsTrigger>
                <TabsTrigger value="model">By model</TabsTrigger>
              </TabsList>
              {(["org_id", "team_id", "key_id", "model"] as Dim[]).map((d) => (
                <TabsContent key={d} value={d}>
                  <Breakdown rows={rows} dim={d} currency={currency} />
                </TabsContent>
              ))}
            </Tabs>
          </Panel>

          {config.data && (
            <Panel title="Pricing" description="Per 1M tokens, from cost.models (defaults apply to unlisted models)">
              <div className="flex flex-wrap gap-3">
                {Object.entries(config.data.cost.models).map(([m, p]) => (
                  <div key={m} className="rounded-lg border px-3 py-2">
                    <div className="font-mono text-xs">{m}</div>
                    <div className="text-sm tabular">
                      {formatCurrency(p.prompt_per_1m, currency)} in · {formatCurrency(p.completion_per_1m, currency)} out
                    </div>
                  </div>
                ))}
                <div className="rounded-lg border border-dashed px-3 py-2">
                  <div className="text-xs text-muted-foreground">default</div>
                  <div className="text-sm tabular">
                    {formatCurrency(config.data.cost.default_prompt_per_1m, currency)} in · {formatCurrency(config.data.cost.default_completion_per_1m, currency)} out
                  </div>
                </div>
              </div>
            </Panel>
          )}
          {config.data?.cost.store === "memory" && (
            <Alert>
              <Info />
              <AlertTitle>In-memory ledger</AlertTitle>
              <AlertDescription>Usage resets when the gateway restarts. Use cost.store: sqlite for durable attribution.</AlertDescription>
            </Alert>
          )}
        </div>
      )}
    </Page>
  )
}
