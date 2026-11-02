import { FileClock } from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router"

import { DataTable, type ColumnDef } from "@/components/data-table"
import { SearchInput, TimeAgo, Timestamp } from "@/components/misc"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState } from "@/components/states"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useAudit, useNodes } from "@/hooks/queries"
import type { AuditEvent } from "@/lib/api/types"
import { shortId } from "@/lib/format"

import { AUDIT_KIND, ControlGate } from "./shared"

export default function FleetAuditPage() {
  const [limit, setLimit] = useState("250")
  const [kind, setKind] = useState("all")
  const [q, setQ] = useState("")
  const audit = useAudit({ limit: Number(limit) })
  const nodes = useNodes()
  const names = useMemo(() => new Map((nodes.data ?? []).map((n) => [n.node_id, n.display_name])), [nodes.data])
  const kinds = useMemo(() => [...new Set((audit.data ?? []).map((e) => e.kind))].sort(), [audit.data])
  const data = useMemo(() => (audit.data ?? []).filter((e) => kind === "all" || e.kind === kind), [audit.data, kind])

  const columns = useMemo<ColumnDef<AuditEvent, unknown>[]>(
    () => [
      {
        accessorKey: "created_at",
        header: "Time",
        cell: ({ getValue }) => (
          <span className="flex flex-col">
            <Timestamp value={getValue() as string} className="text-xs" />
            <span className="text-[11px] text-muted-foreground">
              <TimeAgo value={getValue() as string} />
            </span>
          </span>
        ),
      },
      {
        accessorKey: "kind",
        header: "Event",
        cell: ({ getValue }) => {
          const k = AUDIT_KIND[getValue() as string] ?? { label: getValue() as string, tone: "neutral" as const }
          return (
            <StatusBadge tone={k.tone} size="sm" icon={null}>
              {k.label}
            </StatusBadge>
          )
        },
      },
      {
        accessorKey: "node_id",
        header: "Node",
        cell: ({ getValue }) => {
          const id = getValue() as string
          if (!id) return <span className="text-muted-foreground">control plane</span>
          return (
            <Link className="underline-offset-4 hover:underline" to={`/fleet/nodes/${encodeURIComponent(id)}`}>
              {names.get(id) || <span className="font-mono text-xs">{shortId(id, 16)}</span>}
            </Link>
          )
        },
      },
      { accessorKey: "detail", header: "Detail", cell: ({ getValue }) => <span className="font-mono text-xs text-muted-foreground">{(getValue() as string) || "—"}</span> },
      { accessorKey: "id", header: "#", meta: { align: "right" }, cell: ({ getValue }) => <span className="text-xs text-muted-foreground">{getValue() as number}</span> },
    ],
    [names],
  )

  return (
    <ControlGate title="Fleet audit" description="Durable control-plane events: enrollments, sessions, certificate rotations, revocations and restores. Newest first.">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={q} onChange={setQ} placeholder="Search node, event, detail…" />
        <Select value={kind} onValueChange={setKind}>
          <SelectTrigger size="sm" className="w-full sm:w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All events</SelectItem>
            {kinds.map((k) => (
              <SelectItem key={k} value={k}>
                {AUDIT_KIND[k]?.label ?? k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={limit} onValueChange={setLimit}>
          <SelectTrigger size="sm" className="w-full sm:w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {["100", "250", "1000"].map((l) => (
              <SelectItem key={l} value={l}>
                Last {l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <DataTable
        columns={columns}
        data={data}
        loading={audit.isLoading}
        refreshing={audit.isPlaceholderData}
        globalFilter={q}
        getRowId={(e) => String(e.id)}
        pageSize={50}
        dense
        empty={<EmptyState bordered={false} icon={FileClock} title="No events" />}
      />
    </ControlGate>
  )
}
