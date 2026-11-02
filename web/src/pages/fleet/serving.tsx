import { useMutation } from "@tanstack/react-query"
import { Ban, Calculator, LifeBuoy, Waypoints } from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog } from "@/components/misc"
import { KeyValueGrid, Panel } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useControl, useControlOverview, useCrl, useNodes, useProjection, useStatus } from "@/hooks/queries"
import type { PlacementResult, ProjectionEndpoint } from "@/lib/api/types"
import { formatBytes, shortId } from "@/lib/format"
import { useAuth } from "@/providers/auth-provider"

import { ControlGate } from "./shared"

function PlacementSimulator() {
  const { api } = useControl()
  const [gib, setGib] = useState("16")
  const [count, setCount] = useState("1")
  const [digest, setDigest] = useState("")
  const [result, setResult] = useState<PlacementResult | null | undefined>(undefined)
  const nodes = useNodes()
  const place = useMutation({
    mutationFn: () =>
      api!.place({
        required_vram_bytes: Math.round(Number(gib) * 2 ** 30),
        count: Math.max(1, Number(count) || 1),
        artifact_digest: digest.trim() || undefined,
      }),
    onSuccess: setResult,
    onError: (e) => toast.error("Placement query failed", { description: e instanceof Error ? e.message : String(e) }),
  })
  const node = result ? nodes.data?.find((n) => n.node_id === result.node_id) : null
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Calculator className="size-4" /> Placement simulator
        </span>
      }
      description="Asks meridian-control where a new engine would land. Prefers nodes already holding the artifact, then lower queue depth, then most headroom. Read-only — nothing is scheduled."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          place.mutate()
        }}
      >
        <FieldGroup className="gap-4">
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_2fr]">
            <Field>
              <FieldLabel htmlFor="pl-vram">VRAM per device (GiB)</FieldLabel>
              <Input id="pl-vram" type="number" min={0} step={0.5} value={gib} onChange={(e) => setGib(e.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="pl-count">Devices</FieldLabel>
              <Input id="pl-count" type="number" min={1} max={16} value={count} onChange={(e) => setCount(e.target.value)} />
              <FieldDescription>&gt;1 requires one NVLink group</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="pl-digest">Artifact digest (optional)</FieldLabel>
              <Input id="pl-digest" value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:…" className="font-mono text-xs" />
            </Field>
          </div>
          <Button type="submit" className="w-fit" disabled={place.isPending}>
            {place.isPending && <Spinner data-icon="inline-start" />}
            Find placement
          </Button>
        </FieldGroup>
      </form>
      {result !== undefined && (
        <div className="mt-5">
          {result === null ? (
            <Alert>
              <Ban />
              <AlertTitle>No node fits</AlertTitle>
              <AlertDescription>
                No online node has {count} device(s) with ≥ {gib} GiB allocatable{Number(count) > 1 ? " in a single NVLink group" : ""}.
              </AlertDescription>
            </Alert>
          ) : (
            <div className="rounded-lg border bg-accent/40 p-4">
              <div className="mb-3 flex items-center gap-2">
                <StatusBadge tone="good">Placement found</StatusBadge>
              </div>
              <KeyValueGrid
                columns={3}
                items={[
                  {
                    label: "Node",
                    value: (
                      <Link className="underline-offset-4 hover:underline" to={`/fleet/nodes/${encodeURIComponent(result.node_id)}`}>
                        {node?.display_name || shortId(result.node_id, 16)}
                      </Link>
                    ),
                  },
                  { label: "Devices", value: result.device_ids.join(", "), mono: true },
                  { label: "Headroom on chosen devices", value: formatBytes(result.allocatable_vram_bytes) },
                ]}
              />
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

function RestorePanel() {
  const { api } = useControl()
  const ov = useControlOverview()
  const [open, setOpen] = useState(false)
  const [epoch, setEpoch] = useState("")
  const restore = useMutation({
    mutationFn: () => api!.restore(Number(epoch)),
    onSuccess: (r) => {
      toast.success(`Restore recorded · incarnation ${r.incarnation}`, { description: "Epochs below the floor can no longer re-admit a fenced agent." })
      setOpen(false)
      setEpoch("")
      void ov.refetch()
    },
    onError: (e) => toast.error("Restore failed", { description: e instanceof Error ? e.message : String(e) }),
  })
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <LifeBuoy className="size-4" /> Restore runbook
        </span>
      }
      description="Run after restoring the control-plane database from backup."
    >
      <div className="space-y-4">
        <KeyValueGrid
          items={[
            { label: "Incarnation", value: ov.data?.incarnation ?? "—" },
            { label: "Epoch floor", value: ov.data?.epoch_floor ?? "—" },
          ]}
        />
        <p className="text-sm text-muted-foreground">
          Raises the fencing-epoch floor past the last epoch issued before the incident so a restored (older) epoch can never re-admit a fenced agent.
        </p>
        <Field>
          <FieldLabel htmlFor="restore-epoch">High-water epoch</FieldLabel>
          <div className="flex gap-2">
            <Input id="restore-epoch" type="number" min={0} value={epoch} onChange={(e) => setEpoch(e.target.value)} placeholder="e.g. 1842" className="w-40" />
            <Button variant="destructive" disabled={epoch === "" || Number(epoch) < 0} onClick={() => setOpen(true)}>
              Record restore
            </Button>
          </div>
        </Field>
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Record a control-plane restore?"
        description={<p>Increments the incarnation and sets the epoch floor to at least {epoch}. Every agent re-establishes its session with a higher epoch.</p>}
        confirmLabel="Record restore"
        destructive
        typeToConfirm="restore"
        pending={restore.isPending}
        onConfirm={() => restore.mutate()}
      />
    </Panel>
  )
}

export default function ServingPage() {
  const projection = useProjection()
  const crl = useCrl()
  const status = useStatus()
  const nodes = useNodes()
  const { can } = useAuth()
  const registered = useMemo(() => new Set((status.data?.backends ?? []).map((b) => b.name)), [status.data])
  const nodeName = useMemo(() => new Map((nodes.data ?? []).map((n) => [n.node_id, n.display_name])), [nodes.data])
  const eps = projection.data ?? []

  const columns = useMemo<ColumnDef<ProjectionEndpoint, unknown>[]>(
    () => [
      { accessorKey: "engine_id", header: "Engine", cell: ({ getValue }) => <span className="font-mono text-xs font-medium">{getValue() as string}</span> },
      {
        accessorKey: "node_id",
        header: "Node",
        cell: ({ getValue }) => (
          <Link className="underline-offset-4 hover:underline" to={`/fleet/nodes/${encodeURIComponent(getValue() as string)}`}>
            {nodeName.get(getValue() as string) || shortId(getValue() as string, 14)}
          </Link>
        ),
      },
      { accessorKey: "model", header: "Model", cell: ({ getValue }) => <span className="font-mono text-xs">{(getValue() as string) || "—"}</span> },
      { accessorKey: "endpoint", header: "Endpoint", cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}</span> },
      {
        accessorKey: "routable",
        header: "Projection",
        cell: ({ getValue }) => (
          <StatusBadge tone={getValue() ? "good" : "muted"} size="sm">
            {getValue() ? "Routable" : "Not routable"}
          </StatusBadge>
        ),
      },
      {
        id: "gateway",
        header: "This gateway",
        accessorFn: (e) => registered.has(`managed:${e.node_id}:${e.engine_id}`),
        cell: ({ row }) => {
          const name = `managed:${row.original.node_id}:${row.original.engine_id}`
          if (!can("view_ops")) return "—"
          return registered.has(name) ? (
            <Link to={`/backends/${encodeURIComponent(name)}`}>
              <StatusBadge tone="good" size="sm" className="hover:underline">
                Registered
              </StatusBadge>
            </Link>
          ) : (
            row.original.routable ? (
              <StatusBadge tone="warning" size="sm">
                Not synced
              </StatusBadge>
            ) : (
              <span className="text-xs text-muted-foreground">not routable</span>
            )
          )
        },
      },
    ],
    [registered, nodeName, can],
  )

  const routable = eps.filter((e) => e.routable).length
  const synced = eps.filter((e) => registered.has(`managed:${e.node_id}:${e.engine_id}`)).length

  return (
    <ControlGate
      title="Serving & placement"
      description="The serving projection is the control plane's verdict on which managed engines may receive traffic. Gateways poll it and register routable engines as backends."
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Ready endpoints" icon={Waypoints} value={eps.length} hint="engines reporting Ready with an endpoint" loading={projection.isLoading} />
        <StatCard label="Routable" value={routable} tone={routable === eps.length ? "good" : "warning"} hint="valid lease and not revoked" loading={projection.isLoading} />
        <StatCard label="Registered in this gateway" value={synced} tone={synced < routable ? "warning" : "neutral"} hint="managed backends after projection sync" loading={projection.isLoading} />
      </div>
      <Panel title="Serving projection" description="GET /admin/projection">
        <DataTable
          columns={columns}
          data={eps}
          loading={projection.isLoading}
          refreshing={projection.isPlaceholderData}
          getRowId={(e) => `${e.node_id}:${e.engine_id}`}
          empty={<EmptyState bordered={false} icon={Waypoints} title="No ready engines" description="Publish desired state to a managed-host node to start an engine." />}
        />
      </Panel>
      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <PlacementSimulator />
        <div className="flex flex-col gap-4">
          <Panel title="Certificate revocation list" description="For the TLS-terminating edge to reject at handshake" contentClassName="px-0">
            {(crl.data ?? []).length === 0 ? (
              <p className="px-4 text-sm text-muted-foreground">No revoked nodes.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-4 text-xs">Node</TableHead>
                    <TableHead className="pr-4 text-xs">Serial</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {crl.data!.map((r) => (
                    <TableRow key={r.node_id}>
                      <TableCell className="pl-4 font-mono text-xs">{shortId(r.node_id, 18)}</TableCell>
                      <TableCell className="pr-4 font-mono text-xs text-muted-foreground">{r.serial ? shortId(r.serial, 18) : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Panel>
          <RestorePanel />
        </div>
      </div>
    </ControlGate>
  )
}
