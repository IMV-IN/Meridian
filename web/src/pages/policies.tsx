import { Ban, Check, Gauge, ScrollText, ShieldCheck, Wallet } from "lucide-react"
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router"

import { BarList } from "@/components/charts"
import { Hint, SearchInput, TimeAgo } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel } from "@/components/page"
import { Meter, StatCard } from "@/components/stat"
import { StatusBadge, type Tone } from "@/components/status-badge"
import { EmptyState, ErrorState, FeatureOffState, LoadingGrid } from "@/components/states"
import { Card } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useBudgets, useConfig, useMetrics } from "@/hooks/queries"
import type { BudgetRow, ConfigSummary } from "@/lib/api/types"
import { formatNumber, formatPercent, formatSeconds, titleCase } from "@/lib/format"
import { select, sumBy } from "@/lib/prometheus"
import { cn } from "@/lib/utils"

function utilTone(u: number | null): Tone {
  if (u === null) return "neutral"
  if (u >= 1) return "critical"
  if (u >= 0.8) return "warning"
  return "good"
}

function BudgetsTab() {
  const budgets = useBudgets()
  const metrics = useMetrics()
  const [q, setQ] = useState("")
  const [level, setLevel] = useState("all")

  const scopes = useMemo(() => {
    const m = new Map<string, { level: BudgetRow["scope_level"]; id: string; rows: BudgetRow[]; max: number }>()
    for (const r of budgets.data?.rows ?? []) {
      const k = `${r.scope_level}:${r.scope_id}`
      const cur = m.get(k) ?? { level: r.scope_level, id: r.scope_id, rows: [], max: 0 }
      cur.rows.push(r)
      cur.max = Math.max(cur.max, r.utilization ?? 0)
      m.set(k, cur)
    }
    return [...m.values()].sort((a, b) => b.max - a.max)
  }, [budgets.data])

  const rejections = useMemo(
    () =>
      metrics.parsed
        ? select(metrics.parsed, "meridian_budget_rejections_total").map((s) => ({
            key: `${s.labels.level}-${s.labels.period}`,
            label: `${titleCase(s.labels.level)} · ${s.labels.period}`,
            value: s.value,
          }))
        : [],
    [metrics.parsed],
  )

  if (budgets.isError && !budgets.data) return <ErrorState error={budgets.error} onRetry={() => budgets.refetch()} />
  if (!budgets.data) return <LoadingGrid count={3} className="xl:grid-cols-3" />
  if (!budgets.data.enabled)
    return (
      <FeatureOffState
        icon={Wallet}
        title="Budgets are not enabled"
        description="Hard daily/monthly token and request caps per org, team, user or API key. Requests over a cap get 429 with Retry-After until the period rolls over."
        snippet={`budgets:\n  enabled: true\n  store: sqlite\n  orgs:\n    acme: { daily: { tokens: 2000000, requests: 5000 } }\n  teams:\n    acme/search: { monthly: { tokens: 20000000 } }`}
      />
    )

  const rows = budgets.data.rows
  const exhausted = rows.filter((r) => (r.utilization ?? 0) >= 1).length
  const warning = rows.filter((r) => (r.utilization ?? 0) >= 0.8 && (r.utilization ?? 0) < 1).length
  const shown = scopes.filter((s) => (level === "all" || s.level === level) && (!q || s.id.toLowerCase().includes(q.toLowerCase())))

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard label="Scopes with caps" icon={Wallet} value={scopes.length} hint={`${rows.length} caps across org/team/user/key`} />
        <StatCard label="Near limit" value={warning} tone={warning ? "warning" : "good"} hint="caps at ≥ 80% this period" />
        <StatCard label="Exhausted" value={exhausted} tone={exhausted ? "critical" : "good"} hint="requests are being rejected (429)" />
        <StatCard
          label="Rejections"
          icon={Ban}
          value={formatNumber(rejections.reduce((a, r) => a + r.value, 0))}
          hint="since gateway start"
        />
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={q} onChange={setQ} placeholder="Search scope…" />
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger size="sm" className="w-full sm:w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All levels</SelectItem>
            <SelectItem value="org">Orgs</SelectItem>
            <SelectItem value="team">Teams</SelectItem>
            <SelectItem value="user">Users</SelectItem>
            <SelectItem value="key">API keys</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground sm:ml-auto">
          Updated <TimeAgo value={budgets.data.generated_at ?? null} /> · consumption is reconciled to actual tokens after each response
        </span>
      </div>
      {shown.length === 0 ? (
        <EmptyState icon={Wallet} title="No scopes match" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((s) => (
            <Card key={`${s.level}:${s.id}`} className="gap-3 px-4 py-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs text-muted-foreground uppercase">{s.level}</div>
                  <div className="truncate font-mono text-sm font-medium">{s.id}</div>
                </div>
                <StatusBadge tone={utilTone(s.max)} size="sm">
                  {s.max >= 1 ? "Exhausted" : s.max >= 0.8 ? "Near limit" : "Healthy"}
                </StatusBadge>
              </div>
              <ul className="space-y-3">
                {s.rows.map((r) => (
                  <li key={`${r.period}-${r.metric}`} className="space-y-1.5">
                    <div className="flex items-baseline justify-between gap-2 text-xs">
                      <span>
                        <span className="font-medium">{titleCase(r.period)}</span> <span className="text-muted-foreground">{r.metric}</span>
                      </span>
                      <span className="tabular">
                        {formatNumber(r.consumed)} <span className="text-muted-foreground">/ {formatNumber(r.cap)}</span>
                      </span>
                    </div>
                    <Meter value={r.utilization} label={`${s.id} ${r.period} ${r.metric}`} />
                    <div className="flex justify-between text-[11px] text-muted-foreground">
                      <span>{formatPercent(r.utilization, 0)} used</span>
                      <span>
                        resets <TimeAgo value={r.resets_at} />
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          ))}
        </div>
      )}
      <Panel title="Rejections by level" description="meridian_budget_rejections_total">
        <BarList color="var(--status-serious)" items={rejections} />
      </Panel>
    </div>
  )
}

function RateLimitTab({ cfg }: { cfg: ConfigSummary }) {
  const metrics = useMetrics()
  const rl = cfg.rate_limit
  const rejections = metrics.parsed ? sumBy(metrics.parsed, "meridian_ratelimit_rejections_total", "scope") : {}
  if (!rl.enabled)
    return (
      <FeatureOffState
        icon={Gauge}
        title="Rate limiting is not enabled"
        description="Token buckets protect the fleet from bursts: a fleet-wide bucket, per-model buckets, then per-org (or per-IP when anonymous)."
        snippet={`rate_limit:\n  enabled: true\n  token_capacity: 50      # burst\n  token_refill_rate: 20   # sustained req/s\n  global: { token_capacity: 500, token_refill_rate: 200 }`}
      />
    )
  const rows: { scope: string; key: string; capacity: number; refill: number; note?: string }[] = []
  if (rl.global) rows.push({ scope: "global", key: "fleet-wide", capacity: rl.global.token_capacity, refill: rl.global.token_refill_rate, note: "checked first" })
  for (const [m, c] of Object.entries(rl.models)) if (c) rows.push({ scope: "model", key: m, capacity: c.token_capacity, refill: c.token_refill_rate })
  rows.push({ scope: "org / ip", key: "default", capacity: rl.token_capacity, refill: rl.token_refill_rate, note: "per org, or per client IP without auth" })
  for (const [o, c] of Object.entries(rl.org_overrides)) if (c) rows.push({ scope: "org", key: o, capacity: c.token_capacity, refill: c.token_refill_rate, note: "override" })
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel className="xl:col-span-2" title="Token buckets" description="Every request costs one token; buckets refill continuously. All applicable scopes (global → model → org/ip) must admit — a rejection returns 429 + Retry-After and consumes nothing from any bucket." contentClassName="px-0">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-4 text-xs">Scope</TableHead>
              <TableHead className="text-xs">Applies to</TableHead>
              <TableHead className="text-right text-xs">
                <Hint content="Bucket capacity: how many requests can arrive at once.">Burst</Hint>
              </TableHead>
              <TableHead className="text-right text-xs">
                <Hint content="Refill rate: long-run sustained requests per second.">Sustained</Hint>
              </TableHead>
              <TableHead className="pr-4 text-xs">Notes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="tabular">
            {rows.map((r) => (
              <TableRow key={`${r.scope}-${r.key}`}>
                <TableCell className="pl-4">
                  <StatusBadge tone={r.scope === "global" ? "warning" : "info"} size="sm" icon={null}>
                    {r.scope}
                  </StatusBadge>
                </TableCell>
                <TableCell className={cn(r.scope === "model" && "font-mono text-xs", "font-medium")}>{r.key}</TableCell>
                <TableCell className="text-right">{formatNumber(r.capacity)}</TableCell>
                <TableCell className="text-right">{formatNumber(r.refill)}/s</TableCell>
                <TableCell className="pr-4 text-xs text-muted-foreground">{r.note ?? ""}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
      <div className="flex flex-col gap-4">
        <Panel title="Rejections by scope" description="Since gateway start">
          <BarList color="var(--status-warning)" items={Object.entries(rejections).map(([k, v]) => ({ key: k, label: titleCase(k), value: v }))} />
        </Panel>
        <Panel title="Bucket store">
          <KeyValueGrid
            items={[
              { label: "Max buckets", value: formatNumber(rl.max_buckets) },
              { label: "Idle eviction", value: formatSeconds(rl.idle_ttl_s) },
            ]}
          />
        </Panel>
      </div>
    </div>
  )
}

const PII_POLICIES: { id: string; title: string; body: string }[] = [
  { id: "block", title: "Block", body: "Reject requests containing a detected entity (400). Nothing reaches the backend." },
  { id: "redact_and_replace", title: "Redact and replace", body: "Replace matches with typed placeholders before forwarding; the model never sees the value." },
  { id: "redact_for_logs", title: "Redact for logs", body: "Forward unchanged; only audit metadata records that an entity was present." },
  { id: "audit_only", title: "Audit only", body: "Count detections for compliance reporting without changing traffic." },
]

const ENTITY_LABEL: Record<string, string> = {
  aadhaar: "Aadhaar (Verhoeff-validated)",
  pan: "PAN",
  gstin: "GSTIN",
  ifsc: "IFSC",
  upi: "UPI ID",
  phone_in: "Indian phone number",
}

function PiiTab({ cfg }: { cfg: ConfigSummary }) {
  const metrics = useMetrics()
  const byEntity = metrics.parsed ? sumBy(metrics.parsed, "meridian_pii_detections_total", "entity") : {}
  const pii = cfg.pii
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel
        className="xl:col-span-2"
        title="PII protection · India pack"
        description="Scans request messages before routing. Matched values are never logged — only entity type counts."
        actions={<StatusBadge tone={pii.enabled ? "good" : "neutral"}>{pii.enabled ? "Enforcing" : "Disabled"}</StatusBadge>}
      >
        <div className="space-y-5">
          <div className="grid gap-2 sm:grid-cols-2">
            {PII_POLICIES.map((p) => {
              const active = pii.enabled && pii.policy === p.id
              return (
                <div key={p.id} className={cn("rounded-lg border p-3", active && "border-primary/60 bg-accent/60")}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{p.title}</span>
                    {active && (
                      <StatusBadge tone="good" size="sm">
                        Active
                      </StatusBadge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{p.body}</p>
                </div>
              )
            })}
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Entities</div>
            <div className="flex flex-wrap gap-2">
              {pii.supported_entities.map((e) => {
                const on = pii.enabled && pii.entities.includes(e)
                return (
                  <span key={e} className={cn("inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs", !on && "text-muted-foreground")}>
                    {on ? <Check className="size-3.5 text-good-ink" /> : <span className="size-3.5" />}
                    {ENTITY_LABEL[e] ?? e}
                  </span>
                )
              })}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">Keys can override the global policy with <code>pii_policy</code> (set when creating a key).</p>
        </div>
      </Panel>
      <div className="flex flex-col gap-4">
        <Panel title="Detections by entity" description="Since gateway start">
          <BarList items={Object.entries(byEntity).map(([k, v]) => ({ key: k, label: ENTITY_LABEL[k] ?? k, value: v }))} />
        </Panel>
        <Panel title={<span className="flex items-center gap-2"><ScrollText className="size-4" /> Audit trail</span>}>
          <KeyValueGrid
            items={[
              { label: "Request log", value: "JSONL · metadata only" },
              { label: "Audit bus", value: cfg.audit_bus.enabled ? `Kafka · ${cfg.audit_bus.topic}` : "Off" },
              { label: "Prompts logged", value: "Never" },
              { label: "PII values logged", value: "Never" },
            ]}
          />
        </Panel>
      </div>
    </div>
  )
}

export default function PoliciesPage() {
  const config = useConfig()
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") ?? "budgets"
  return (
    <Page>
      <PageHeader
        title="Policies"
        description="Guardrails enforced on every request before it reaches a backend. Edit them in the gateway config and reload; this page shows the effective state."
      />
      <Tabs value={tab} onValueChange={(t) => setParams(t === "budgets" ? {} : { tab: t }, { replace: true })} className="gap-4">
        <TabsList>
          <TabsTrigger value="budgets">
            <Wallet /> Budgets
          </TabsTrigger>
          <TabsTrigger value="rate-limits">
            <Gauge /> Rate limits
          </TabsTrigger>
          <TabsTrigger value="pii">
            <ShieldCheck /> PII &amp; compliance
          </TabsTrigger>
        </TabsList>
        <TabsContent value="budgets">
          <BudgetsTab />
        </TabsContent>
        <TabsContent value="rate-limits">
          {config.data ? <RateLimitTab cfg={config.data} /> : config.isError ? <ErrorState error={config.error} /> : <LoadingGrid count={2} />}
        </TabsContent>
        <TabsContent value="pii">
          {config.data ? <PiiTab cfg={config.data} /> : config.isError ? <ErrorState error={config.error} /> : <LoadingGrid count={2} />}
        </TabsContent>
      </Tabs>
    </Page>
  )
}
