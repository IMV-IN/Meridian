import { LayoutGrid, Rows3, Server } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"

import { SegmentedBar } from "@/components/charts"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { SearchInput } from "@/components/misc"
import { Page, PageHeader, TagList } from "@/components/page"
import { Meter, Sparkline } from "@/components/stat"
import { BACKEND_HEALTH_META, StatusBadge } from "@/components/status-badge"
import { EmptyState, ErrorState, LoadingGrid } from "@/components/states"
import { Card } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useStatus } from "@/hooks/queries"
import type { BackendStatus } from "@/lib/api/types"
import { backendLabel, formatEwma, formatNumber, formatPercent, titleCase } from "@/lib/format"
import { backendHealth, type BackendHealth } from "@/lib/telemetry"
import { useTelemetry } from "@/providers/telemetry-provider"

type View = "table" | "cards"

function NameCell({ b }: { b: BackendStatus }) {
  const l = backendLabel(b.name)
  return (
    <div className="flex max-w-[260px] min-w-0 flex-col" title={b.name}>
      <span className="truncate font-medium">{l.primary}</span>
      <span className="truncate text-xs text-muted-foreground">{l.secondary ?? b.url}</span>
    </div>
  )
}

function HealthBadge({ b }: { b: BackendStatus }) {
  const h = BACKEND_HEALTH_META[backendHealth(b)]
  return (
    <StatusBadge tone={h.tone} size="sm">
      {h.label}
    </StatusBadge>
  )
}

function TelemetryCell({ b }: { b: BackendStatus }) {
  if (b.gpu_mem_util === null && b.queue_depth === null && b.tokens_per_sec === null)
    return <span className="text-xs text-muted-foreground">not configured</span>
  return (
    <div className="flex min-w-32 flex-col gap-1">
      {b.gpu_mem_util !== null && (
        <div className="flex items-center gap-2">
          <Meter value={b.gpu_mem_util} size="sm" warnAt={0.85} critAt={0.95} label="GPU memory" />
          <span className="w-9 text-right text-[11px] tabular text-muted-foreground">{formatPercent(b.gpu_mem_util, 0)}</span>
        </div>
      )}
      <span className="text-[11px] text-muted-foreground tabular">
        {b.queue_depth !== null && `q ${b.queue_depth}`}
        {b.queue_depth !== null && b.tokens_per_sec !== null && " · "}
        {b.tokens_per_sec !== null && `${formatNumber(b.tokens_per_sec)} tok/s`}
      </span>
    </div>
  )
}

const columns: ColumnDef<BackendStatus, unknown>[] = [
  { accessorKey: "name", header: "Backend", cell: ({ row }) => <NameCell b={row.original} /> },
  {
    id: "health",
    accessorFn: (b) => backendHealth(b),
    header: "Status",
    cell: ({ row }) => <HealthBadge b={row.original} />,
  },
  {
    accessorKey: "engine",
    header: "Engine",
    cell: ({ row }) => <span className="text-[13px]">{row.original.engine}</span>,
  },
  {
    accessorKey: "model",
    header: "Model",
    cell: ({ row }) =>
      row.original.model ? (
        <span className="font-mono text-xs">{row.original.model}</span>
      ) : (
        <span className="text-xs text-muted-foreground">any</span>
      ),
  },
  {
    accessorKey: "tags",
    header: "Tags",
    enableSorting: false,
    cell: ({ row }) => <TagList tags={row.original.tags} max={3} className="max-w-56" />,
  },
  { accessorKey: "weight", header: "Weight", meta: { align: "right" } },
  { accessorKey: "inflight", header: "In flight", meta: { align: "right" } },
  {
    accessorKey: "ewma_latency_ms",
    header: "EWMA",
    meta: { align: "right" },
    cell: ({ getValue }) => formatEwma(getValue() as number),
  },
  {
    id: "circuit",
    accessorFn: (b) => b.circuit?.state ?? "n/a",
    header: "Circuit",
    cell: ({ row }) => {
      const c = row.original.circuit
      if (!c) return <span className="text-xs text-muted-foreground">off</span>
      const tone = c.state === "closed" ? "good" : c.state === "open" ? "critical" : "warning"
      return (
        <StatusBadge tone={tone} size="sm" icon={null}>
          {titleCase(c.state)}
          {c.consecutive_failures > 0 && <span className="text-muted-foreground"> · {c.consecutive_failures}</span>}
        </StatusBadge>
      )
    },
  },
  {
    id: "telemetry",
    header: "Telemetry",
    enableSorting: false,
    cell: ({ row }) => <TelemetryCell b={row.original} />,
  },
]

function BackendCard({ b }: { b: BackendStatus }) {
  const { backends } = useTelemetry()
  const trend = (backends[b.name] ?? []).map((p) => p.ewma)
  const l = backendLabel(b.name)
  return (
    <Link
      to={`/backends/${encodeURIComponent(b.name)}`}
      className="group rounded-xl focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
    >
      <Card className="h-full gap-0 py-0 transition-shadow group-hover:shadow-md">
        <div className="flex items-start justify-between gap-3 p-4 pb-2">
          <div className="min-w-0" title={b.name}>
            <div className="truncate font-semibold">{l.primary}</div>
            <div className="truncate text-xs text-muted-foreground">
              {b.engine} · {b.model || "any model"}
            </div>
          </div>
          <HealthBadge b={b} />
        </div>
        <div className="grid grid-cols-3 gap-2 px-4 py-2">
          <div>
            <div className="text-[11px] text-muted-foreground">In flight</div>
            <div className="text-lg font-semibold tabular">{b.inflight}</div>
          </div>
          <div>
            <div className="text-[11px] text-muted-foreground">EWMA</div>
            <div className="text-lg font-semibold">{formatEwma(b.ewma_latency_ms)}</div>
          </div>
          <div>
            <div className="text-[11px] text-muted-foreground">Weight</div>
            <div className="text-lg font-semibold tabular">{b.weight}</div>
          </div>
        </div>
        <div className="px-0 pt-1">
          <Sparkline values={trend} height={36} />
        </div>
        <div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
          <TagList tags={b.tags} max={3} />
          <span className="shrink-0 truncate font-mono text-[11px] text-muted-foreground">{b.url.replace(/^https?:\/\//, "")}</span>
        </div>
      </Card>
    </Link>
  )
}

const HEALTH_ORDER: BackendHealth[] = ["healthy", "degraded", "idle", "circuit_open", "unhealthy"]

export default function BackendsPage() {
  const status = useStatus()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get("q") ?? "")
  const view = (params.get("view") as View) || "table"
  const health = params.get("health") ?? "all"
  const source = params.get("source") ?? "all"
  const engine = params.get("engine") ?? "all"

  const setParam = (k: string, v: string, def: string) => {
    const next = new URLSearchParams(params)
    if (v === def) next.delete(k)
    else next.set(k, v)
    setParams(next, { replace: true })
  }

  const all = useMemo(() => status.data?.backends ?? [], [status.data])
  const engines = useMemo(() => [...new Set(all.map((b) => b.engine))].sort(), [all])
  const filtered = useMemo(
    () =>
      all.filter(
        (b) =>
          (health === "all" || backendHealth(b) === health) &&
          (engine === "all" || b.engine === engine) &&
          (source === "all" || (source === "managed") === b.name.startsWith("managed:")),
      ),
    [all, health, engine, source],
  )
  const counts = useMemo(() => {
    const c: Record<BackendHealth, number> = { healthy: 0, degraded: 0, idle: 0, circuit_open: 0, unhealthy: 0 }
    for (const b of all) c[backendHealth(b)] += 1
    return c
  }, [all])

  return (
    <Page>
      <PageHeader
        title="Backends"
        description={
          status.data
            ? `${all.length} registered · ${counts.healthy} in rotation · routing with ${titleCase(status.data.strategy)}`
            : "Inference backends behind this gateway"
        }
        actions={
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={view}
            onValueChange={(v) => v && setParam("view", v, "table")}
            aria-label="View"
          >
            <ToggleGroupItem value="table" aria-label="Table view">
              <Rows3 />
            </ToggleGroupItem>
            <ToggleGroupItem value="cards" aria-label="Card view">
              <LayoutGrid />
            </ToggleGroupItem>
          </ToggleGroup>
        }
      />

      {all.length > 0 && (
        <Card className="px-4 py-3">
          <SegmentedBar
            parts={HEALTH_ORDER.map((h) => ({
              key: h,
              label: BACKEND_HEALTH_META[h].label,
              value: counts[h],
              color: {
                healthy: "var(--status-good)",
                degraded: "var(--status-warning)",
                idle: "var(--muted-foreground)",
                circuit_open: "var(--status-serious)",
                unhealthy: "var(--status-critical)",
              }[h],
            }))}
          />
        </Card>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <SearchInput value={query} onChange={setQuery} placeholder="Search name, model, tag, URL…" />
        <Select value={health} onValueChange={(v) => setParam("health", v, "all")}>
          <SelectTrigger size="sm" className="w-full sm:w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {HEALTH_ORDER.map((h) => (
              <SelectItem key={h} value={h}>
                {BACKEND_HEALTH_META[h].label} ({counts[h]})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={engine} onValueChange={(v) => setParam("engine", v, "all")}>
          <SelectTrigger size="sm" className="w-full sm:w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All engines</SelectItem>
            {engines.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={source} onValueChange={(v) => setParam("source", v, "all")}>
          <SelectTrigger size="sm" className="w-full sm:w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Static + managed</SelectItem>
            <SelectItem value="static">Static (config)</SelectItem>
            <SelectItem value="managed">Managed (control plane)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {status.isError && !status.data ? (
        <ErrorState error={status.error} onRetry={() => status.refetch()} />
      ) : view === "table" ? (
        <DataTable
          columns={columns}
          data={filtered}
          loading={status.isLoading}
          globalFilter={query}
          getRowId={(b) => b.name}
          onRowClick={(b) => navigate(`/backends/${encodeURIComponent(b.name)}`)}
          empty={
            <EmptyState
              icon={Server}
              bordered={false}
              title={all.length ? "No backends match these filters" : "No backends registered"}
              description={
                all.length
                  ? "Clear the search or status filters."
                  : "Add backends to the gateway config, or connect GPU nodes through meridian-control."
              }
            />
          }
        />
      ) : !status.data ? (
        <LoadingGrid count={6} className="xl:grid-cols-3" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered
            .filter((b) => {
              const q = query.toLowerCase().trim()
              return !q || [b.name, b.model, b.engine, b.url, ...b.tags].some((v) => v.toLowerCase().includes(q))
            })
            .map((b) => (
              <BackendCard key={b.name} b={b} />
            ))}
        </div>
      )}
    </Page>
  )
}
