import { Download, ExternalLink, Pause, Play, ScrollText } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router"

import { BarList, ColumnChart, SegmentedBar } from "@/components/charts"
import { CodeBlock, CopyText } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { SearchInput, TimeAgo, Timestamp } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge, httpStatusTone } from "@/components/status-badge"
import { EmptyState, ErrorState } from "@/components/states"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { useRecentRequests } from "@/hooks/queries"
import type { RequestRecord } from "@/lib/api/types"
import { backendLabel, formatMs, formatNumber, formatPercent, percentile, statusClass } from "@/lib/format"

const ERROR_EXPLAIN: Record<string, string> = {
  upstream_5xx: "The backend answered with a 5xx. Counts as a passive health failure and toward the circuit breaker.",
  circuit_open: "Rejected pre-flight: the backend's circuit breaker is open after consecutive failures. No upstream call was made.",
  client_disconnect: "The client closed the connection mid-stream; Meridian cancelled the upstream request.",
  stream_read_timeout: "No SSE bytes arrived within timeouts.stream_read; the stream was terminated with an error event.",
  ReadTimeout: "The backend accepted the connection but did not respond within timeouts.read.",
  ConnectError: "Could not open a TCP connection to the backend.",
  ConnectTimeout: "The TCP connect did not complete within timeouts.connect.",
  RemoteProtocolError: "The backend closed the connection or sent a malformed HTTP response.",
}

type StatusFilter = "all" | "2xx" | "4xx" | "5xx" | "errors"

function toCsv(rows: RequestRecord[]): string {
  const cols: (keyof RequestRecord)[] = [
    "timestamp",
    "request_id",
    "status_code",
    "chosen_backend",
    "model",
    "org_id",
    "team_id",
    "tier",
    "stream",
    "latency_ms",
    "error_type",
  ]
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n")
}

function download(name: string, mime: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: mime }))
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function RequestSheet({ record, onClose }: { record: RequestRecord | null; onClose: () => void }) {
  return (
    <Sheet open={!!record} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 sm:max-w-lg">
        {record && (
          <>
            <SheetHeader className="border-b">
              <div className="flex items-center gap-2">
                <StatusBadge tone={httpStatusTone(record.status_code)}>{record.status_code}</StatusBadge>
                <span className="text-xs text-muted-foreground">
                  <TimeAgo value={record.timestamp} />
                </span>
              </div>
              <SheetTitle className="font-mono text-base">{record.request_id}</SheetTitle>
              <SheetDescription>
                <Timestamp value={record.timestamp} /> · {record.stream ? "streaming" : "non-streaming"} ·{" "}
                {formatMs(record.latency_ms)}
              </SheetDescription>
            </SheetHeader>
            <div className="scrollbar-thin flex-1 space-y-6 overflow-y-auto p-4">
              {record.error_type && (
                <div className="space-y-1.5 rounded-lg border bg-muted/40 p-3">
                  <div className="text-sm font-medium">{record.error_type}</div>
                  <p className="text-sm text-muted-foreground">
                    {ERROR_EXPLAIN[record.error_type] ?? "Upstream transport error recorded by the gateway."}
                  </p>
                </div>
              )}
              <KeyValueGrid
                items={[
                  {
                    label: "Backend",
                    value: (
                      <Link className="underline-offset-4 hover:underline" to={`/backends/${encodeURIComponent(record.chosen_backend)}`}>
                        {backendLabel(record.chosen_backend).primary}
                      </Link>
                    ),
                  },
                  { label: "Model", value: record.model, mono: true },
                  { label: "Org", value: record.org_id ?? "—" },
                  { label: "Team", value: record.team_id ?? "—" },
                  { label: "Tier", value: record.tier ?? "—" },
                  { label: "Latency", value: formatMs(record.latency_ms) },
                ]}
              />
              <div className="space-y-2">
                <div className="text-sm font-medium">Find it in the audit log</div>
                <p className="text-xs text-muted-foreground">
                  The JSONL request log is metadata-only — prompts and completions are never written.
                </p>
                <CodeBlock code={`grep '"${record.request_id}"' meridian_requests.jsonl | jq .`} language="shell" />
              </div>
              <div className="space-y-2">
                <div className="text-sm font-medium">Raw record</div>
                <CodeBlock code={JSON.stringify(record, null, 2)} language="json" />
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}

export default function RequestsPage() {
  const q = useRecentRequests()
  const [params, setParams] = useSearchParams()
  const [frozen, setFrozen] = useState<RequestRecord[] | null>(null)
  const [selected, setSelected] = useState<RequestRecord | null>(null)
  const statusF = (params.get("status") as StatusFilter) || "all"
  const backendF = params.get("backend") ?? "all"
  const modeF = params.get("mode") ?? "all"
  const orgF = params.get("org") ?? "all"

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (!v || v === "all") next.delete(k)
    else next.set(k, v)
    setParams(next, { replace: true })
  }

  const search = params.get("q") ?? ""
  const data = useMemo(() => frozen ?? q.data ?? [], [frozen, q.data])
  const backends = useMemo(() => [...new Set(data.map((r) => r.chosen_backend))].sort(), [data])
  const orgs = useMemo(() => [...new Set(data.map((r) => r.org_id ?? "—"))].sort(), [data])

  const filtered = useMemo(
    () =>
      data.filter((r) => {
        const cls = statusClass(r.status_code)
        if (statusF === "errors" && r.status_code < 400) return false
        if (statusF !== "all" && statusF !== "errors" && cls !== statusF) return false
        if (backendF !== "all" && r.chosen_backend !== backendF) return false
        if (modeF !== "all" && (modeF === "stream") !== r.stream) return false
        if (orgF !== "all" && (r.org_id ?? "—") !== orgF) return false
        return true
      }),
    [data, statusF, backendF, modeF, orgF],
  )

  const stats = useMemo(() => {
    const lat = filtered.map((r) => r.latency_ms).sort((a, b) => a - b)
    const classes = { "2xx": 0, "4xx": 0, "5xx": 0, other: 0 }
    for (const r of filtered) {
      const c = statusClass(r.status_code)
      if (c === "2xx" || c === "4xx" || c === "5xx") classes[c] += 1
      else classes.other += 1
    }
    const bins = [50, 100, 250, 500, 1000, 2500, 5000, 10000, Number.POSITIVE_INFINITY]
    let prev = 0
    const dist = bins.map((le) => {
      const n = lat.filter((v) => v > prev && v <= le).length
      const label = Number.isFinite(le) ? `≤${le >= 1000 ? `${le / 1000}s` : `${le}ms`}` : ">10s"
      prev = le
      return { bucket: label, count: n }
    })
    const perBackend: Record<string, number> = {}
    for (const r of filtered) perBackend[r.chosen_backend] = (perBackend[r.chosen_backend] ?? 0) + 1
    return {
      byBackend: Object.entries(perBackend).sort((a, b) => b[1] - a[1]),
      p50: percentile(lat, 50),
      p95: percentile(lat, 95),
      errRate: filtered.length ? filtered.filter((r) => r.status_code >= 400).length / filtered.length : null,
      streamShare: filtered.length ? filtered.filter((r) => r.stream).length / filtered.length : null,
      classes,
      dist,
    }
  }, [filtered])

  const columns = useMemo<ColumnDef<RequestRecord, unknown>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Time",
        cell: ({ getValue }) => <Timestamp value={getValue() as string} date={false} className="text-xs" />,
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
      {
        accessorKey: "request_id",
        header: "Request ID",
        cell: ({ getValue }) => <CopyText value={getValue() as string} />,
      },
      {
        accessorKey: "chosen_backend",
        header: "Backend",
        cell: ({ getValue }) => <span className="block max-w-44 truncate">{backendLabel(getValue() as string).primary}</span>,
      },
      { accessorKey: "model", header: "Model", cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}</span> },
      {
        id: "tenant",
        accessorFn: (r) => [r.org_id, r.team_id].filter(Boolean).join(" / "),
        header: "Org / team",
        cell: ({ getValue }) => (getValue() as string) || <span className="text-muted-foreground">anonymous</span>,
      },
      { accessorKey: "tier", header: "Tier", cell: ({ getValue }) => (getValue() as string) ?? <span className="text-muted-foreground">—</span> },
      {
        accessorKey: "stream",
        header: "Mode",
        cell: ({ getValue }) => (
          <span className="text-xs text-muted-foreground">{getValue() ? "stream" : "sync"}</span>
        ),
      },
      {
        accessorKey: "latency_ms",
        header: "Latency",
        meta: { align: "right" },
        cell: ({ getValue }) => formatMs(getValue() as number),
      },
      {
        accessorKey: "error_type",
        header: "Error",
        cell: ({ getValue }) =>
          getValue() ? <span className="text-xs">{getValue() as string}</span> : <span className="text-muted-foreground">—</span>,
      },
    ],
    [],
  )

  const classColors = {
    "2xx": "var(--status-good)",
    "4xx": "var(--status-warning)",
    "5xx": "var(--status-critical)",
    other: "var(--muted-foreground)",
  }

  return (
    <Page>
      <PageHeader
        title="Requests"
        description="The gateway keeps the latest 100 requests in memory. The JSONL request log and the audit bus are the durable, metadata-only record."
        actions={
          <>
            <Button
              variant={frozen ? "default" : "outline"}
              size="sm"
              onClick={() => setFrozen(frozen ? null : (q.data ?? []))}
              aria-pressed={!!frozen}
            >
              {frozen ? <Play data-icon="inline-start" /> : <Pause data-icon="inline-start" />}
              {frozen ? "Resume tail" : "Freeze"}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" disabled={!filtered.length}>
                  <Download data-icon="inline-start" /> Export
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => download("meridian_requests.csv", "text/csv", toCsv(filtered))}>
                  CSV ({filtered.length} rows)
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => download("meridian_requests.json", "application/json", JSON.stringify(filtered, null, 2))}
                >
                  JSON ({filtered.length} rows)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard label="Requests shown" icon={ScrollText} value={formatNumber(filtered.length)} hint={`of ${data.length} buffered${frozen ? " · frozen" : ""}`} />
        <StatCard
          label="Error rate"
          value={formatPercent(stats.errRate)}
          tone={stats.errRate === null ? "neutral" : stats.errRate > 0.05 ? "critical" : stats.errRate > 0.01 ? "warning" : "good"}
          hint="4xx + 5xx share"
        />
        <StatCard label="Latency p95" value={formatMs(stats.p95)} hint={`p50 ${formatMs(stats.p50)}`} />
        <StatCard label="Streaming" value={formatPercent(stats.streamShare, 0)} hint="of requests used SSE" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Response classes" className="xl:col-span-1">
          <SegmentedBar
            parts={(["2xx", "4xx", "5xx", "other"] as const).map((k) => ({
              key: k,
              label: k,
              value: stats.classes[k],
              color: classColors[k],
            }))}
          />
          <div className="mt-4 border-t pt-3">
            <div className="mb-2 text-xs text-muted-foreground">By backend</div>
            <BarList
              items={stats.byBackend.slice(0, 4).map(([name, n]) => ({
                key: name,
                label: backendLabel(name).primary,
                value: n,
              }))}
              onSelect={(name) => setParam("backend", name)}
            />
          </div>
        </Panel>
        <Panel title="Latency distribution" description="Requests in view" className="xl:col-span-2">
          <ColumnChart data={stats.dist} xKey="bucket" yKey="count" label="Requests" height={120} />
        </Panel>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <SearchInput
          value={search}
          onChange={(v) => setParam("q", v)}
          placeholder="Request ID, org, backend, error…"
          className="sm:w-72"
        />
        <Select value={statusF} onValueChange={(v) => setParam("status", v)}>
          <SelectTrigger size="sm" className="w-full sm:w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="errors">Errors only</SelectItem>
            <SelectItem value="2xx">2xx</SelectItem>
            <SelectItem value="4xx">4xx</SelectItem>
            <SelectItem value="5xx">5xx</SelectItem>
          </SelectContent>
        </Select>
        <Select value={backendF} onValueChange={(v) => setParam("backend", v)}>
          <SelectTrigger size="sm" className="w-full sm:w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All backends</SelectItem>
            {backends.map((b) => (
              <SelectItem key={b} value={b}>
                {backendLabel(b).primary}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={orgF} onValueChange={(v) => setParam("org", v)}>
          <SelectTrigger size="sm" className="w-full sm:w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All orgs</SelectItem>
            {orgs.map((o) => (
              <SelectItem key={o} value={o}>
                {o === "—" ? "anonymous" : o}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={modeF} onValueChange={(v) => setParam("mode", v)}>
          <SelectTrigger size="sm" className="w-full sm:w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any mode</SelectItem>
            <SelectItem value="stream">Streaming</SelectItem>
            <SelectItem value="sync">Non-stream</SelectItem>
          </SelectContent>
        </Select>
        {(statusF !== "all" || backendF !== "all" || modeF !== "all" || orgF !== "all" || search) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
          >
            Reset
          </Button>
        )}
      </div>

      {q.isError && !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={filtered}
          loading={q.isLoading}
          globalFilter={search}
          getRowId={(r) => r.request_id}
          onRowClick={setSelected}
          dense
          pageSize={50}
          empty={
            <EmptyState
              bordered={false}
              icon={ScrollText}
              title={data.length ? "No requests match" : "No requests yet"}
              description={
                data.length ? (
                  "Adjust the filters or search."
                ) : (
                  <>
                    Send one from the{" "}
                    <Link to="/playground" className="underline underline-offset-2">
                      playground <ExternalLink className="inline size-3" />
                    </Link>
                    .
                  </>
                )
              }
            />
          }
        />
      )}
      <RequestSheet record={selected} onClose={() => setSelected(null)} />
    </Page>
  )
}
