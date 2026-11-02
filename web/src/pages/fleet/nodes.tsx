import { Cpu, GitCompareArrows, HardDrive, Plus, Router, Ticket } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useNavigate } from "react-router"

import { SegmentedBar } from "@/components/charts"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { SearchInput, TimeAgo } from "@/components/misc"
import { Panel } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useControlOverview, useNodes } from "@/hooks/queries"
import type { NodeSummary } from "@/lib/api/types"
import { formatBytes, formatNumber } from "@/lib/format"

import { ControlGate, LeaseText, NodeStatusBadge } from "./shared"

const columns: ColumnDef<NodeSummary, unknown>[] = [
  {
    id: "node",
    accessorFn: (n) => `${n.display_name} ${n.node_id}`,
    header: "Node",
    cell: ({ row }) => (
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium">{row.original.display_name || "unnamed host"}</span>
        <span className="truncate font-mono text-[11px] text-muted-foreground">{row.original.node_id}</span>
      </div>
    ),
  },
  { accessorKey: "status", header: "Status", cell: ({ row }) => <NodeStatusBadge status={row.original.status} size="sm" /> },
  {
    accessorKey: "lease_expires_at",
    header: "Lease",
    cell: ({ row }) => (
      <span className="text-xs">
        <LeaseText expires={row.original.lease_expires_at} status={row.original.status} />
      </span>
    ),
  },
  {
    id: "engines",
    accessorFn: (n) => n.engines.ready,
    header: "Engines",
    cell: ({ row }) => {
      const e = row.original.engines
      if (!e.total) return <span className="text-xs text-muted-foreground">none</span>
      return (
        <span className="flex items-center gap-2">
          <span className="tabular">
            {e.ready}
            <span className="text-muted-foreground">/{e.total} ready</span>
          </span>
          {e.failed > 0 && (
            <StatusBadge tone="critical" size="sm">
              {e.failed} failed
            </StatusBadge>
          )}
        </span>
      )
    },
  },
  {
    id: "generation",
    accessorFn: (n) => n.desired_generation,
    header: "Generation",
    cell: ({ row }) => {
      const n = row.original
      const drift = n.observed_generation !== null && n.observed_generation < n.desired_generation
      return (
        <span className="flex items-center gap-2 tabular">
          <span>
            {n.observed_generation ?? "—"}
            <span className="text-muted-foreground"> / {n.desired_generation}</span>
          </span>
          {drift && (
            <StatusBadge tone="warning" size="sm" icon={GitCompareArrows}>
              converging
            </StatusBadge>
          )}
        </span>
      )
    },
  },
  {
    id: "vram",
    accessorFn: (n) => n.capacity?.allocatable_vram_bytes ?? -1,
    header: "Free VRAM",
    meta: { align: "right" },
    cell: ({ row }) =>
      row.original.capacity ? (
        <span>
          {formatBytes(row.original.capacity.allocatable_vram_bytes, 0)}
          <span className="text-xs text-muted-foreground"> · {row.original.capacity.devices} dev</span>
        </span>
      ) : (
        <span className="text-xs text-muted-foreground">unreported</span>
      ),
  },
  {
    id: "queue",
    accessorFn: (n) => n.capacity?.queue_depth ?? -1,
    header: "Queue",
    meta: { align: "right" },
    cell: ({ row }) => (row.original.capacity?.queue_depth ?? null) === null ? "—" : formatNumber(row.original.capacity!.queue_depth),
  },
  { accessorKey: "fencing_epoch", header: "Epoch", meta: { align: "right" } },
  {
    accessorKey: "observed_at",
    header: "Last observed",
    cell: ({ getValue }) => <TimeAgo value={getValue() as string | null} className="text-xs" />,
  },
]

export default function NodesPage() {
  const nodes = useNodes()
  const ov = useControlOverview()
  const navigate = useNavigate()
  const [q, setQ] = useState("")
  const [status, setStatus] = useState("all")
  const data = useMemo(() => (nodes.data ?? []).filter((n) => status === "all" || n.status === status), [nodes.data, status])
  const d = ov.data

  return (
    <ControlGate
      title="GPU nodes"
      description="Hosts running the meridian-node agent. A node serves traffic only while its lease is valid and an engine reports Ready."
      actions={
        <Button size="sm" asChild>
          <Link to="/fleet/enrollment?mint=1">
            <Plus data-icon="inline-start" /> Enroll node
          </Link>
        </Button>
      }
    >
      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard
          label="Nodes online"
          icon={Cpu}
          value={
            <>
              {d?.nodes.by_status.online ?? 0}
              <span className="text-base font-normal text-muted-foreground">/{d?.nodes.total ?? 0}</span>
            </>
          }
          tone={!d ? "neutral" : (d.nodes.by_status.online ?? 0) === d.nodes.total ? "good" : "warning"}
          hint={`${d?.nodes.by_status.offline ?? 0} lease expired · ${d?.nodes.by_status.revoked ?? 0} revoked`}
          loading={!d}
        />
        <StatCard
          label="Engines ready"
          icon={Router}
          value={
            <>
              {d?.engines.ready ?? 0}
              <span className="text-base font-normal text-muted-foreground">/{d?.engines.total ?? 0}</span>
            </>
          }
          tone={d && d.engines.failed > 0 ? "critical" : "neutral"}
          hint={`${d?.routable_endpoints ?? 0} routable endpoints · ${d?.engines.failed ?? 0} failed`}
          loading={!d}
        />
        <StatCard label="Allocatable VRAM" icon={HardDrive} value={d ? formatBytes(d.allocatable_vram_bytes, 0) : "—"} hint="free for placement on online nodes" loading={!d} />
        <StatCard
          label="Pending approvals"
          icon={Ticket}
          value={d?.claims_pending ?? 0}
          tone={d && d.claims_pending > 0 ? "warning" : "neutral"}
          hint={<Link to="/fleet/enrollment" className="underline-offset-2 hover:underline">Review enrollment queue</Link>}
          loading={!d}
        />
      </div>

      {d && d.nodes.total > 0 && (
        <Panel title="Fleet state" description={`Control-plane incarnation ${d.incarnation} · epoch floor ${d.epoch_floor} · lease TTL ${d.settings.lease_ttl_seconds}s · mTLS ${d.settings.require_mtls ? "enforced" : "off"}`}>
          <SegmentedBar
            parts={[
              { key: "online", label: "Online", value: d.nodes.by_status.online ?? 0, color: "var(--status-good)" },
              { key: "enrolled", label: "Awaiting session", value: d.nodes.by_status.enrolled ?? 0, color: "var(--series-1)" },
              { key: "offline", label: "Lease expired", value: d.nodes.by_status.offline ?? 0, color: "var(--status-serious)" },
              { key: "revoked", label: "Revoked", value: d.nodes.by_status.revoked ?? 0, color: "var(--status-critical)" },
            ]}
          />
        </Panel>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={q} onChange={setQ} placeholder="Search hostname or node ID…" />
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger size="sm" className="w-full sm:w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="online">Online</SelectItem>
            <SelectItem value="offline">Lease expired</SelectItem>
            <SelectItem value="enrolled">Awaiting session</SelectItem>
            <SelectItem value="revoked">Revoked</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <DataTable
        columns={columns}
        data={data}
        loading={nodes.isLoading}
        refreshing={nodes.isPlaceholderData}
        globalFilter={q}
        getRowId={(n) => n.node_id}
        onRowClick={(n) => navigate(`/fleet/nodes/${encodeURIComponent(n.node_id)}`)}
        empty={
          <EmptyState
            bordered={false}
            icon={Cpu}
            title={nodes.data?.length ? "No nodes match" : "No nodes enrolled yet"}
            description={nodes.data?.length ? "Adjust the filters." : "Mint a join token and run the agent on a GPU host."}
          >
            {!nodes.data?.length && (
              <Button size="sm" asChild>
                <Link to="/fleet/enrollment?mint=1">Mint a join token</Link>
              </Button>
            )}
          </EmptyState>
        }
      />
    </ControlGate>
  )
}
