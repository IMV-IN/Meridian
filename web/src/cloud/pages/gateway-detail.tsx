import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Network, Timer, Trash2, TriangleAlert, Zap } from "lucide-react"
import { useState } from "react"
import { useNavigate, useParams } from "react-router"
import { toast } from "sonner"

import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, TimeAgo, Timestamp } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel, TagList } from "@/components/page"
import { Meter, StatCard } from "@/components/stat"
import { BACKEND_HEALTH_META, StatusBadge, httpStatusTone } from "@/components/status-badge"
import { EmptyState, ErrorState, LoadingGrid } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import type { BackendStatus, RequestRecord } from "@/lib/api/types"
import { backendLabel, formatEwma, formatMs, formatNumber, formatPercent, titleCase } from "@/lib/format"
import { backendHealth } from "@/lib/telemetry"

import { atLeast, cloud } from "../api"
import { GatewayStatusBadge } from "../components"
import { useGateway } from "../hooks"
import { useWorkspace } from "../session"

const backendColumns: ColumnDef<BackendStatus, unknown>[] = [
  {
    accessorKey: "name",
    header: "Backend",
    cell: ({ row }) => {
      const l = backendLabel(row.original.name)
      return (
        <div className="flex max-w-60 flex-col" title={row.original.name}>
          <span className="truncate font-medium">{l.primary}</span>
          <span className="truncate text-xs text-muted-foreground">
            {row.original.engine} · {row.original.model || "any model"}
          </span>
        </div>
      )
    },
  },
  {
    id: "health",
    accessorFn: (b) => backendHealth(b),
    header: "Status",
    cell: ({ row }) => {
      const h = BACKEND_HEALTH_META[backendHealth(row.original)]
      return (
        <StatusBadge tone={h.tone} size="sm">
          {h.label}
        </StatusBadge>
      )
    },
  },
  { accessorKey: "tags", header: "Tags", enableSorting: false, cell: ({ row }) => <TagList tags={row.original.tags} max={3} /> },
  { accessorKey: "inflight", header: "In flight", meta: { align: "right" } },
  { accessorKey: "ewma_latency_ms", header: "EWMA", meta: { align: "right" }, cell: ({ getValue }) => formatEwma(getValue() as number) },
  {
    id: "circuit",
    accessorFn: (b) => b.circuit?.state ?? "off",
    header: "Circuit",
    cell: ({ row }) => (row.original.circuit ? titleCase(row.original.circuit.state) : <span className="text-muted-foreground">off</span>),
  },
]

const requestColumns: ColumnDef<RequestRecord, unknown>[] = [
  { accessorKey: "timestamp", header: "Time", cell: ({ getValue }) => <Timestamp value={getValue() as string} date={false} className="text-xs" /> },
  {
    accessorKey: "status_code",
    header: "Status",
    cell: ({ row }) => (
      <StatusBadge tone={httpStatusTone(row.original.status_code)} size="sm">
        {row.original.status_code}
      </StatusBadge>
    ),
  },
  { accessorKey: "request_id", header: "Request ID", cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}</span> },
  { accessorKey: "chosen_backend", header: "Backend", cell: ({ getValue }) => backendLabel(getValue() as string).primary },
  { accessorKey: "model", header: "Model", cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}</span> },
  { id: "tenant", accessorFn: (r) => [r.org_id, r.team_id].filter(Boolean).join(" / "), header: "Org / team" },
  { accessorKey: "latency_ms", header: "Latency", meta: { align: "right" }, cell: ({ getValue }) => formatMs(getValue() as number) },
  { accessorKey: "error_type", header: "Error", cell: ({ getValue }) => (getValue() as string) ?? <span className="text-muted-foreground">—</span> },
]

export default function GatewayDetailPage() {
  const { gatewayId = "" } = useParams()
  const id = decodeURIComponent(gatewayId)
  const ws = useWorkspace()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const g = useGateway(id)
  const [forget, setForget] = useState(false)
  const remove = useMutation({
    mutationFn: () => cloud.forgetGateway(ws.id, id),
    onSuccess: () => {
      toast.success("Gateway removed from this workspace", { description: "It reappears if it reports again; revoke its token to stop it." })
      void qc.invalidateQueries({ queryKey: ["cloud", "gateways", ws.id] })
      navigate("/gateways")
    },
  })

  if (g.isError && !g.data) return <Page><ErrorState error={g.error} onRetry={() => g.refetch()} /></Page>
  if (!g.data) return <Page><LoadingGrid count={4} /></Page>
  const d = g.data
  const r = d.report
  const backends = r.status?.backends ?? []
  const win = d.window
  const canary = r.status?.canary

  return (
    <Page>
      <PageHeader
        eyebrow="Gateway"
        title={
          <span className="flex flex-wrap items-center gap-3">
            {d.name} <GatewayStatusBadge g={d} />
          </span>
        }
        description={
          <span className="flex flex-wrap gap-x-3">
            <span className="font-mono text-xs">{d.gateway_id}</span>
            <span>v{d.version}</span>
            <span>
              last report <TimeAgo value={d.last_seen_at} />
            </span>
          </span>
        }
        actions={
          atLeast(ws.role, "admin") && (
            <Button variant="outline" size="sm" onClick={() => setForget(true)}>
              <Trash2 data-icon="inline-start" /> Remove
            </Button>
          )
        }
      />
      {!d.online && (
        <Alert>
          <TriangleAlert />
          <AlertTitle>No recent report</AlertTitle>
          <AlertDescription>
            This gateway stopped reporting. It may be down, or unable to reach Meridian Cloud — the data below is from its last report.
          </AlertDescription>
        </Alert>
      )}
      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard label="Throughput" icon={Zap} value={formatNumber(win.rps ?? null, { digits: 1 })} unit="req/s" hint={win.seconds ? `over ${Math.round(win.seconds)}s` : "waiting for a second report"} />
        <StatCard label="Latency p95" icon={Timer} value={formatMs(win.p95_ms ?? null)} hint={`p50 ${formatMs(win.p50_ms ?? null)}`} />
        <StatCard
          label="5xx in interval"
          value={formatNumber(win.errors ?? null)}
          tone={(win.errors ?? 0) > 0 ? "critical" : "good"}
          hint={`${formatNumber(win.client_errors ?? 0)} 4xx · ${formatNumber(win.requests ?? 0)} requests`}
        />
        <StatCard
          label="Backends healthy"
          icon={Network}
          value={
            <>
              {d.backends_healthy}
              <span className="text-base font-normal text-muted-foreground">/{d.backends_total}</span>
            </>
          }
          tone={d.backends_total && d.backends_healthy < d.backends_total ? "warning" : "good"}
          hint={`strategy ${d.strategy ? titleCase(d.strategy) : "—"}`}
        />
      </div>
      <Panel title="Backends" description="As of the last report">
        <DataTable columns={backendColumns} data={backends} getRowId={(b) => b.name} dense empty={<EmptyState bordered={false} title="No backends" />} />
      </Panel>
      <div className="grid gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Recent requests" description="Metadata only — no prompts or completions are sent to Meridian Cloud">
          <DataTable columns={requestColumns} data={r.recent_requests ?? []} getRowId={(x) => x.request_id} dense pageSize={10} empty={<EmptyState bordered={false} title="No requests yet" />} />
        </Panel>
        <div className="flex flex-col gap-4">
          {canary && (
            <Panel title="Canary rollout">
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span>{canary.rolled_back ? "Rolled back" : `Step ${canary.step + 1}`}</span>
                  <span className="font-medium tabular">{canary.weight}%</span>
                </div>
                <Meter value={canary.weight / 100} warnAt={2} label="canary weight" />
                <div className="text-xs text-muted-foreground">
                  window errors {canary.window_error_rate === null ? "—" : formatPercent(canary.window_error_rate)} · {canary.window_samples} samples
                </div>
              </div>
            </Panel>
          )}
          <Panel title="Gateway">
            <KeyValueGrid
              items={[
                { label: "First seen", value: <Timestamp value={d.first_seen_at} /> },
                { label: "Report interval", value: `${r.interval_s ?? "—"}s` },
                { label: "Requests (lifetime)", value: formatNumber(Object.values(r.totals ?? {}).reduce((a, b) => a + b, 0)) },
                { label: "Features", value: Object.entries(r.features ?? {}).filter(([, on]) => on).map(([k]) => titleCase(k)).join(", ") || "—" },
              ]}
            />
          </Panel>
        </div>
      </div>
      <ConfirmDialog
        open={forget}
        onOpenChange={setForget}
        title={`Remove ${d.name}?`}
        description={<p>Removes it from this list. If the gateway is still running with a valid token it will reappear at its next report — revoke the token in Settings → Gateway tokens to disconnect it.</p>}
        confirmLabel="Remove"
        destructive
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </Page>
  )
}
