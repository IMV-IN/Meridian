import { ArrowRight, Check, CircleSlash, FlaskConical, Layers, Lock, Pin, Route, ShieldAlert, Split, TriangleAlert } from "lucide-react"
import { Fragment, useMemo } from "react"
import { Link } from "react-router"

import { Hint } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel, TagList } from "@/components/page"
import { Meter } from "@/components/stat"
import { StatusBadge, type Tone } from "@/components/status-badge"
import { ErrorState, LoadingGrid } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useConfig, useRecentRequests, useStatus } from "@/hooks/queries"
import type { BackendStatus, ConfigSummary } from "@/lib/api/types"
import { backendLabel, formatNumber, formatPercent, formatSeconds, titleCase } from "@/lib/format"
import { backendHealth } from "@/lib/telemetry"
import { cn } from "@/lib/utils"

const STRATEGY_INFO: Record<string, { title: string; body: string }> = {
  least_inflight: {
    title: "Least in-flight",
    body: "Picks the eligible backend with the fewest requests currently in flight. Simple and robust for homogeneous fleets.",
  },
  weighted_round_robin: {
    title: "Weighted round robin",
    body: "Rotates across eligible backends in proportion to their configured weight.",
  },
  ewma_latency: {
    title: "EWMA latency",
    body: "Prefers the backend with the lowest exponentially-weighted moving-average latency.",
  },
  token_aware: {
    title: "Token-aware",
    body: "Estimates request cost from prompt size and max_tokens, then picks the backend with the lowest predicted completion time for its cost-weighted queue. Reduces head-of-line blocking for mixed workloads.",
  },
  cache_affinity: {
    title: "Cache affinity (experimental)",
    body: "Token-aware routing plus bounded prefix predictions so repeated prefixes land where the KV cache is likely warm; corrected by engine-reported cached tokens.",
  },
}

function matching(backends: BackendStatus[], tags: string[]) {
  return backends.filter((b) => tags.length > 0 && tags.every((t) => b.tags.includes(t)))
}

function BackendChips({ list }: { list: BackendStatus[] }) {
  if (!list.length)
    return (
      <StatusBadge tone="critical" size="sm">
        No matching backend
      </StatusBadge>
    )
  return (
    <span className="flex flex-wrap gap-1">
      {list.map((b) => {
        const h = backendHealth(b)
        const tone: Tone = h === "healthy" ? "good" : h === "idle" ? "muted" : h === "degraded" ? "warning" : "critical"
        return (
          <Link key={b.name} to={`/backends/${encodeURIComponent(b.name)}`}>
            <StatusBadge tone={tone} size="sm" icon={null} className="hover:underline">
              {backendLabel(b.name).primary}
            </StatusBadge>
          </Link>
        )
      })}
    </span>
  )
}

function Pipeline({ cfg, canaryWeight }: { cfg: ConfigSummary; canaryWeight?: number }) {
  const stages = [
    {
      icon: Lock,
      title: "Isolation",
      on: cfg.isolation.mode === "dedicated",
      summary:
        cfg.isolation.mode === "dedicated"
          ? `${Object.keys(cfg.isolation.pools).length} pinned org${Object.keys(cfg.isolation.pools).length === 1 ? "" : "s"}`
          : "Shared pool",
      href: "#isolation",
    },
    {
      icon: Split,
      title: "Canary split",
      on: cfg.canary.enabled,
      summary: cfg.canary.enabled ? `${canaryWeight ?? cfg.canary.start_weight}% to canary` : "Off",
      href: "#canary",
    },
    {
      icon: Layers,
      title: "Workload tiering",
      on: cfg.tiering.enabled,
      summary: cfg.tiering.enabled ? "3 pools by request shape" : "Off",
      href: "#tiering",
    },
    {
      icon: Pin,
      title: "Session affinity",
      on: cfg.session_affinity.enabled,
      summary: cfg.session_affinity.enabled ? `${formatNumber(cfg.session_affinity.active_sessions ?? 0)} pinned` : "Off",
      href: "#affinity",
    },
    {
      icon: Route,
      title: "Strategy",
      on: true,
      summary: STRATEGY_INFO[cfg.gateway.strategy]?.title ?? cfg.gateway.strategy,
      href: "#strategy",
    },
  ]
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[repeat(5,minmax(0,1fr))]">
      {stages.map((s, i) => (
        <Fragment key={s.title}>
          <a
            href={s.href}
            className={cn(
              "group relative flex flex-col gap-2 rounded-xl border bg-card p-3.5 transition-colors hover:border-ring/50",
              !s.on && "bg-card/50",
            )}
          >
            <div className="flex items-center justify-between">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-md",
                  s.on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                )}
              >
                <s.icon className="size-3.5" />
              </span>
              <span className="text-[11px] text-muted-foreground tabular">Step {i + 1}</span>
            </div>
            <div>
              <div className={cn("text-sm font-medium", !s.on && "text-muted-foreground")}>{s.title}</div>
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                {s.on ? <Check className="size-3 text-good-ink" /> : <CircleSlash className="size-3" />}
                {s.summary}
              </div>
            </div>
            {i < stages.length - 1 && (
              <ArrowRight className="absolute top-1/2 -right-2.5 z-10 hidden size-4 -translate-y-1/2 rounded-full bg-background text-muted-foreground lg:block" />
            )}
          </a>
        </Fragment>
      ))}
    </div>
  )
}

function CanaryPanel({ cfg, backends }: { cfg: ConfigSummary; backends: BackendStatus[] }) {
  const status = useStatus()
  const records = useRecentRequests()
  const c = status.data?.canary
  const canaryPool = matching(backends, cfg.canary.canary_tags)
  const stablePool = matching(backends, cfg.canary.stable_tags)
  const observed = useMemo(() => {
    const names = new Set(canaryPool.map((b) => b.name))
    const stable = new Set(stablePool.map((b) => b.name))
    const split = (records.data ?? []).filter((r) => names.has(r.chosen_backend) || stable.has(r.chosen_backend))
    if (!split.length) return null
    return split.filter((r) => names.has(r.chosen_backend)).length / split.length
  }, [records.data, canaryPool, stablePool])

  if (!cfg.canary.enabled || !c) {
    return (
      <Panel title="Canary rollout" description="Weighted split between two backend tag pools with error-rate auto-rollback">
        <p className="text-sm text-muted-foreground">
          Not enabled. Configure <code className="text-xs">canary:</code> with <code className="text-xs">stable_tags</code>,{" "}
          <code className="text-xs">canary_tags</code> and a <code className="text-xs">steps</code> schedule to roll new engine versions out
          gradually.
        </p>
      </Panel>
    )
  }
  const tone: Tone = c.rolled_back ? "critical" : c.schedule_done ? "good" : "info"
  const label = c.rolled_back ? "Rolled back" : c.schedule_done ? "Schedule complete" : `Step ${c.step + 1} of ${cfg.canary.steps.length}`
  const errRatio = c.window_error_rate === null ? 0 : c.window_error_rate / cfg.canary.rollback_error_rate
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <FlaskConical className="size-4" /> Canary rollout
        </span>
      }
      description="Promotion walks the schedule on a clock; demotion is automatic on error-rate breach"
      actions={<StatusBadge tone={tone}>{label}</StatusBadge>}
    >
      <div className="space-y-6">
        {c.rolled_back && (
          <Alert variant="destructive">
            <ShieldAlert />
            <AlertTitle>Automatic rollback fired</AlertTitle>
            <AlertDescription>
              The canary pool breached {formatPercent(cfg.canary.rollback_error_rate, 0)} errors over at least {cfg.canary.rollback_min_samples} samples.
              Weight is held at 0% until the canary section changes on reload.
            </AlertDescription>
          </Alert>
        )}
        <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
          <div className="space-y-4">
            <div>
              <div className="text-xs text-muted-foreground">Traffic on canary</div>
              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-semibold tracking-tight">{c.weight}%</span>
                <span className="text-sm text-muted-foreground">
                  observed {observed === null ? "—" : formatPercent(observed, 0)} in recent requests
                </span>
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">
                  <Hint content={`Rolling ${formatSeconds(cfg.canary.window_s)} window of canary-pool outcomes; rollback needs ≥ ${cfg.canary.rollback_min_samples} samples.`}>
                    Window error rate
                  </Hint>
                </span>
                <span className="tabular">
                  {c.window_error_rate === null ? "—" : formatPercent(c.window_error_rate)} / {formatPercent(cfg.canary.rollback_error_rate, 0)} threshold
                </span>
              </div>
              <Meter value={errRatio} warnAt={0.6} critAt={1} label="error rate against rollback threshold" />
              <div className="text-xs text-muted-foreground tabular">
                {c.window_samples} of ≥{cfg.canary.rollback_min_samples} samples in window
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <div className="text-xs text-muted-foreground">Schedule</div>
            <ol className="flex gap-[2px]">
              {cfg.canary.steps.map((s, i) => {
                const state = c.rolled_back ? "rolled" : i < c.step || (c.schedule_done && i <= c.step) ? "done" : i === c.step ? "current" : "next"
                return (
                  <li key={i} className="min-w-0 flex-1">
                    <div
                      className={cn(
                        "h-2 first:rounded-l-full",
                        i === 0 && "rounded-l-full",
                        i === cfg.canary.steps.length - 1 && "rounded-r-full",
                        state === "done" && "bg-(--series-1)",
                        state === "current" && "bg-(--series-1) opacity-60",
                        state === "next" && "bg-muted",
                        state === "rolled" && "bg-muted",
                      )}
                    />
                    <div className={cn("mt-2 text-sm font-medium tabular", state === "next" && "text-muted-foreground")}>{s.weight}%</div>
                    <div className="text-xs text-muted-foreground">{s.duration_s ? `for ${formatSeconds(s.duration_s)}` : "hold"}</div>
                    {state === "current" && !c.schedule_done && <div className="flex items-center gap-1 text-[11px] font-medium text-foreground"><span aria-hidden className="size-1.5 rounded-full bg-(--series-1)" />current</div>}
                  </li>
                )
              })}
            </ol>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2 rounded-lg border p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Stable pool</span>
              <TagList tags={cfg.canary.stable_tags} />
            </div>
            <BackendChips list={stablePool} />
          </div>
          <div className="space-y-2 rounded-lg border p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Canary pool</span>
              <TagList tags={cfg.canary.canary_tags} />
            </div>
            <BackendChips list={canaryPool} />
          </div>
        </div>
      </div>
    </Panel>
  )
}

function TieringPanel({ cfg, backends }: { cfg: ConfigSummary; backends: BackendStatus[] }) {
  const records = useRecentRequests()
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of records.data ?? []) if (r.tier) c[r.tier] = (c[r.tier] ?? 0) + 1
    return c
  }, [records.data])
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const t = cfg.tiering
  const rules = [
    { key: "long_prompt", rule: `estimated prompt ≥ ${formatNumber(t.long_prompt_tokens)} tokens`, note: "checked first" },
    { key: "long_decode", rule: `max_tokens ≥ ${formatNumber(t.long_decode_tokens)}`, note: "checked second" },
    { key: "default", rule: "everything else", note: "fallback" },
  ]
  return (
    <Panel title="Workload tiering" description="Practical prefill/decode separation: route by request shape to dedicated tag pools">
      {!t.enabled ? (
        <p className="text-sm text-muted-foreground">
          Not enabled. Set <code className="text-xs">tiering.enabled: true</code> to send long-prompt and long-generation requests to dedicated pools.
        </p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-3">
          {rules.map((r, i) => {
            const tags = t.tiers[r.key] ?? []
            const pool = matching(backends, tags)
            return (
              <div key={r.key} className="flex flex-col gap-3 rounded-lg border p-3.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-sm font-semibold">{titleCase(r.key)}</div>
                    <div className="text-xs text-muted-foreground">
                      {i + 1}. {r.note}
                    </div>
                  </div>
                  <span className="text-right">
                    <span className="block text-lg font-semibold tabular">{total ? formatPercent((counts[r.key] ?? 0) / total, 0) : "—"}</span>
                    <span className="block text-[11px] text-muted-foreground">of recent</span>
                  </span>
                </div>
                <div className="rounded-md bg-muted/60 px-2.5 py-1.5 font-mono text-xs">if {r.rule}</div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  pool <TagList tags={tags} />
                </div>
                <BackendChips list={pool} />
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

function IsolationPanel({ cfg, backends }: { cfg: ConfigSummary; backends: BackendStatus[] }) {
  const iso = cfg.isolation
  const pools = Object.entries(iso.pools)
  const reserved = pools.map(([, tags]) => tags)
  const shared = backends.filter((b) => !reserved.some((tags) => tags.every((t) => b.tags.includes(t))))
  return (
    <Panel
      title="Tenant isolation"
      description={iso.mode === "dedicated" ? "Pinned orgs never leave their pool; unlisted orgs never enter a reserved pool" : "All orgs share every backend"}
      actions={<StatusBadge tone={iso.mode === "dedicated" ? "info" : "neutral"} icon={iso.mode === "dedicated" ? Lock : null}>{titleCase(iso.mode)}</StatusBadge>}
      contentClassName={iso.mode === "dedicated" ? "px-0" : undefined}
    >
      {iso.mode !== "dedicated" ? (
        <p className="text-sm text-muted-foreground">
          Set <code className="text-xs">isolation.mode: dedicated</code> and list <code className="text-xs">pools</code> to give regulated tenants dedicated capacity.
        </p>
      ) : (
        <div className="space-y-3">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4 text-xs">Org</TableHead>
                <TableHead className="text-xs">Pool tags</TableHead>
                <TableHead className="pr-4 text-xs">Backends</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pools.map(([org, tags]) => (
                <TableRow key={org}>
                  <TableCell className="pl-4 font-medium">{org}</TableCell>
                  <TableCell>
                    <TagList tags={tags} />
                  </TableCell>
                  <TableCell className="pr-4">
                    <BackendChips list={matching(backends, tags)} />
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="pl-4 text-muted-foreground">Everyone else</TableCell>
                <TableCell className="text-xs text-muted-foreground">backends outside reserved pools</TableCell>
                <TableCell className="pr-4">
                  <BackendChips list={shared} />
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
          {pools.length === 0 && (
            <div className="px-4">
              <Alert>
                <TriangleAlert />
                <AlertTitle>No pools configured</AlertTitle>
                <AlertDescription>Dedicated mode with no pools isolates nothing — every org is unlisted.</AlertDescription>
              </Alert>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

export default function RoutingPage() {
  const config = useConfig()
  const status = useStatus()
  const cfg = config.data
  const backends = status.data?.backends ?? []

  if (config.isError && !cfg) return <Page><ErrorState error={config.error} onRetry={() => config.refetch()} /></Page>
  if (!cfg) return <Page><LoadingGrid count={5} className="xl:grid-cols-5" /></Page>

  const g = cfg.gateway
  const info = STRATEGY_INFO[g.strategy]
  const tokenAware = g.strategy === "token_aware" || g.strategy === "cache_affinity"
  const sa = cfg.session_affinity

  return (
    <Page>
      <PageHeader
        title="Routing & rollout"
        description="Every request walks the same chain: isolation is a hard boundary, then the canary split, tier preference, session pin, and finally the strategy picks within the remaining pool. Changes take effect on config reload."
      />
      <Pipeline cfg={cfg} canaryWeight={status.data?.canary?.weight} />

      <div id="strategy" className="grid scroll-mt-16 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title={`${info?.title ?? g.strategy} strategy`} description={info?.body}>
          {tokenAware ? (
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/40 p-3 font-mono text-[12.5px] leading-relaxed">
                <div>
                  <span className="text-muted-foreground">cost</span> = prompt_tokens × <b>{g.prefill_weight}</b> + max_tokens × <b>{g.decode_weight}</b>
                </div>
                <div>
                  <span className="text-muted-foreground">score</span> = (inflight_cost + cost) × ewma_latency
                  {g.queue_weight > 0 && <> + queue_depth × <b>{g.queue_weight}</b></>}
                  {g.mem_weight > 0 && <> + gpu_mem_util × <b>{g.mem_weight}</b></>}
                </div>
                <div className="text-muted-foreground">→ lowest score wins; ties break on in-flight count, then name</div>
              </div>
              <KeyValueGrid
                columns={4}
                items={[
                  { label: "Prefill weight", value: g.prefill_weight },
                  { label: "Decode weight", value: g.decode_weight },
                  { label: "Default max_tokens", value: formatNumber(g.default_max_tokens) },
                  { label: "Token estimator", value: g.token_estimator },
                  { label: <Hint content="Penalty per queued request when backend telemetry reports queue depth. 0 = ignore.">Queue weight</Hint>, value: g.queue_weight },
                  { label: <Hint content="Penalty for GPU memory utilization when telemetry reports it. 0 = ignore.">Memory weight</Hint>, value: g.mem_weight },
                  { label: "Max body", value: `${formatNumber(g.max_body_bytes / 1024 / 1024)} MiB` },
                ]}
              />
              {g.strategy === "cache_affinity" && (
                <KeyValueGrid
                  columns={4}
                  items={[
                    { label: "Block chars", value: g.cache_affinity.block_chars },
                    { label: "Checkpoints", value: g.cache_affinity.max_checkpoints },
                    { label: "Prediction TTL", value: formatSeconds(g.cache_affinity.ttl_s) },
                    { label: "Locality weight", value: g.cache_affinity.locality_weight },
                    { label: "Hit threshold", value: g.cache_affinity.hit_threshold },
                    { label: "Min trust", value: g.cache_affinity.min_trust },
                    { label: "Feedback correction", value: g.cache_affinity.feedback_correction ? "On" : "Off" },
                    { label: "Max entries", value: formatNumber(g.cache_affinity.max_entries) },
                  ]}
                />
              )}
            </div>
          ) : (
            <KeyValueGrid items={[{ label: "Parameters", value: "None — the strategy uses live gateway counters only." }]} />
          )}
        </Panel>
        <Panel title="Session affinity" description="KV-affinity lite: keep a conversation on one backend" className="scroll-mt-16" >
          <div id="affinity" className="space-y-4">
            {sa.enabled ? (
              <>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Pinned sessions</span>
                    <span className="tabular">
                      {formatNumber(sa.active_sessions ?? 0)} / {formatNumber(sa.max_sessions)}
                    </span>
                  </div>
                  <Meter value={(sa.active_sessions ?? 0) / sa.max_sessions} label="session table usage" />
                </div>
                <KeyValueGrid
                  items={[
                    { label: "Header", value: sa.header, mono: true },
                    { label: "Sliding TTL", value: formatSeconds(sa.ttl_s) },
                  ]}
                />
                <p className="text-xs text-muted-foreground">Pins are namespaced per org and remap when the pinned backend leaves rotation.</p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Not enabled. Clients send <code className="text-xs">{sa.header}</code> to stick to a backend once{" "}
                <code className="text-xs">session_affinity.enabled</code> is true.
              </p>
            )}
          </div>
        </Panel>
      </div>

      <div id="canary" className="scroll-mt-16">
        <CanaryPanel cfg={cfg} backends={backends} />
      </div>
      <div id="tiering" className="scroll-mt-16">
        <TieringPanel cfg={cfg} backends={backends} />
      </div>
      <div id="isolation" className="grid scroll-mt-16 gap-4 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2">
          <IsolationPanel cfg={cfg} backends={backends} />
        </div>
        <Panel title="Resilience" description="Upstream timeouts, retries and circuit breaking">
          <KeyValueGrid
            items={[
              { label: "Retries", value: cfg.resilience.max_retries ? `${cfg.resilience.max_retries} · non-stream` : "Off" },
              { label: "Backoff base", value: formatSeconds(cfg.resilience.retry_backoff_base) },
              {
                label: "Circuit breaker",
                value: cfg.resilience.circuit_breaker.enabled
                  ? `${cfg.resilience.circuit_breaker.failure_threshold} failures → open ${formatSeconds(cfg.resilience.circuit_breaker.open_seconds)}`
                  : "Off",
              },
              { label: "Health checks", value: `every ${formatSeconds(cfg.health.interval_s)} · ${formatSeconds(cfg.health.timeout_s)} timeout` },
              { label: "Eject / restore", value: `${cfg.health.fail_threshold} fails / ${cfg.health.success_threshold} ok` },
              { label: "Connect · read", value: `${formatSeconds(cfg.timeouts.connect)} · ${formatSeconds(cfg.timeouts.read)}` },
              { label: "Stream read", value: cfg.timeouts.stream_read ? formatSeconds(cfg.timeouts.stream_read) : "= read" },
              { label: "Pool · write", value: `${formatSeconds(cfg.timeouts.pool)} · ${formatSeconds(cfg.timeouts.write)}` },
            ]}
          />
        </Panel>
      </div>
    </Page>
  )
}
