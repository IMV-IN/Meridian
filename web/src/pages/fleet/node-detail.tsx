import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Ban, CircleStop, Cpu, FileCode, Fingerprint, GitBranch, ShieldCheck, Timer, TriangleAlert, Upload } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useParams } from "react-router"
import { toast } from "sonner"

import { BarList } from "@/components/charts"
import { CopyText, JsonView } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, Hint, TimeAgo, Timestamp, useNow } from "@/components/misc"
import { KeyValueGrid, Panel, TagList } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge, enginePhaseTone, isTransitionalPhase } from "@/components/status-badge"
import { EmptyState, ErrorState, LoadingGrid } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { qk, useControl, useNode } from "@/hooks/queries"
import type { NodeDetail, ObservedEngine } from "@/lib/api/types"
import { formatBytes, formatNumber, formatRelative, shortId } from "@/lib/format"
import { cn } from "@/lib/utils"

import { AUDIT_KIND, ControlGate, LeaseText, NodeStatusBadge } from "./shared"

function template(node: NodeDetail): string {
  if (node.desired?.snapshot) {
    return JSON.stringify({ ...node.desired.snapshot, generation: node.desired_generation + 1 }, null, 2)
  }
  return JSON.stringify(
    {
      node_id: node.node_id,
      generation: node.desired_generation + 1,
      assignments: [
        {
          engine_id: "engine_example",
          node_id: node.node_id,
          generation: node.desired_generation + 1,
          spec_revision: 1,
          desired_phase: "Running",
          engine: { kind: "vllm", image_digest: "sha256:…", version: "0.10.2" },
          artifact: { artifact_id: "artifact_example", revision: "…", manifest_digest: "sha256:…", engine_image_digest: "sha256:…" },
          resources: { device_ids: ["GPU-…"], memory_limit_bytes: 8589934592, port: 8000 },
          parameters: { served_model_name: "my-model", max_model_len: 4096 },
          policy: { restart: { max_attempts: 5, backoff_seconds: 30 }, drain_timeout_seconds: 120 },
        },
      ],
    },
    null,
    2,
  )
}

function PublishDesiredDialog({ node, open, onOpenChange }: { node: NodeDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">{open && <PublishDesiredBody node={node} onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  )
}

function PublishDesiredBody({ node, onDone }: { node: NodeDetail; onDone: () => void }) {
  const { api, baseUrl } = useControl()
  const qc = useQueryClient()
  // Seeded once per opening; live node refreshes must not clobber edits.
  const [text, setText] = useState(() => template(node))
  const parsed = useMemo(() => {
    try {
      const v = JSON.parse(text) as Record<string, unknown>
      if (typeof v !== "object" || v === null || Array.isArray(v)) return { error: "Snapshot must be a JSON object" }
      if (typeof v.generation !== "number" || !Number.isInteger(v.generation)) return { error: "`generation` must be an integer" }
      if (v.node_id !== undefined && v.node_id !== node.node_id) return { error: `node_id must be ${node.node_id}` }
      if (!Array.isArray(v.assignments)) return { error: "`assignments` must be an array" }
      return { value: v }
    } catch (e) {
      return { error: e instanceof Error ? e.message : "Invalid JSON" }
    }
  }, [text, node.node_id])
  const gen = parsed.value ? Number(parsed.value.generation) : null
  const publish = useMutation({
    mutationFn: (snap: Record<string, unknown>) => api!.setDesired(node.node_id, snap),
    onSuccess: () => {
      toast.success(`Generation ${gen} published`, { description: "The agent fetches it on its next heartbeat and reconciles." })
      onDone()
      void qc.invalidateQueries({ queryKey: qk.control(baseUrl) })
    },
    onError: (e) => toast.error("Publish failed", { description: e instanceof Error ? e.message : String(e) }),
  })
  return (
    <>
        <DialogHeader>
          <DialogTitle>Publish desired state</DialogTitle>
          <DialogDescription>
            A new generation tells the agent what to run. It must be higher than the current generation ({node.desired_generation}) to be picked up; the
            agent only runs images and artifacts its root-owned policy allows.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          className="scrollbar-thin h-[46vh] resize-none font-mono text-xs leading-relaxed"
          aria-label="Desired state snapshot (JSON)"
          aria-invalid={!!parsed.error}
        />
        {parsed.error ? (
          <p className="text-sm text-destructive">{parsed.error}</p>
        ) : gen !== null && gen <= node.desired_generation ? (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <TriangleAlert className="size-4 text-(--status-warning)" /> Generation {gen} is not newer than {node.desired_generation}; the agent will not see a change.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            Valid · generation {gen} · {Array.isArray(parsed.value?.assignments) ? parsed.value.assignments.length : 0} assignment(s)
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onDone}>
            Cancel
          </Button>
          <Button onClick={() => parsed.value && publish.mutate(parsed.value)} disabled={!parsed.value || publish.isPending}>
            {publish.isPending ? <Spinner data-icon="inline-start" /> : <Upload data-icon="inline-start" />}
            Publish generation {gen ?? ""}
          </Button>
        </DialogFooter>
    </>
  )
}

function StopDialog({ node, open, onOpenChange }: { node: NodeDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>{open && <StopBody node={node} onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  )
}

function StopBody({ node, onDone }: { node: NodeDetail; onDone: () => void }) {
  const { api, baseUrl } = useControl()
  const qc = useQueryClient()
  const engines = node.observation?.engines ?? []
  const [picked, setPicked] = useState<string[]>(() => node.stop_authorized_engine_ids)
  const save = useMutation({
    mutationFn: () => api!.authorizeStop(node.node_id, picked),
    onSuccess: () => {
      toast.success(picked.length ? `Stop authorized for ${picked.length} engine(s)` : "Stop authorizations cleared")
      onDone()
      void qc.invalidateQueries({ queryKey: qk.control(baseUrl) })
    },
    onError: (e) => toast.error("Could not update", { description: e instanceof Error ? e.message : String(e) }),
  })
  return (
    <>
        <DialogHeader>
          <DialogTitle>Authorize engine stops</DialogTitle>
          <DialogDescription>
            Fencing safety: an agent only stops an engine that the control plane explicitly authorized. The list replaces any previous authorization.
          </DialogDescription>
        </DialogHeader>
        {engines.length === 0 ? (
          <p className="text-sm text-muted-foreground">This node reports no engines.</p>
        ) : (
          <ul className="space-y-2">
            {engines.map((e) => (
              <li key={e.engine_id}>
                <label className="flex items-center gap-3 rounded-lg border p-3 hover:bg-muted/40">
                  <Checkbox
                    checked={picked.includes(e.engine_id)}
                    onCheckedChange={(c) => setPicked((p) => (c ? [...p, e.engine_id] : p.filter((x) => x !== e.engine_id)))}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block font-mono text-xs">{e.engine_id}</span>
                    <span className="block text-xs text-muted-foreground">{e.model ?? "—"}</span>
                  </span>
                  <StatusBadge tone={enginePhaseTone(e.phase)} size="sm">
                    {e.phase}
                  </StatusBadge>
                </label>
              </li>
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onDone}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending && <Spinner data-icon="inline-start" />}
            Save authorization
          </Button>
        </DialogFooter>
    </>
  )
}

export default function NodeDetailPage() {
  const { nodeId: raw = "" } = useParams()
  const nodeId = decodeURIComponent(raw)
  const node = useNode(nodeId)
  const { api, baseUrl } = useControl()
  const qc = useQueryClient()
  const [publishOpen, setPublishOpen] = useState(false)
  const [stopOpen, setStopOpen] = useState(false)
  const [revokeOpen, setRevokeOpen] = useState(false)
  const revoke = useMutation({
    mutationFn: () => api!.revoke(nodeId),
    onSuccess: () => {
      toast.success("Node revoked", { description: "Its certificate is on the CRL and every further call is rejected." })
      setRevokeOpen(false)
      void qc.invalidateQueries({ queryKey: qk.control(baseUrl) })
    },
    onError: (e) => toast.error("Revoke failed", { description: e instanceof Error ? e.message : String(e) }),
  })

  const n = node.data
  const now = useNow(30_000)
  const stopIds = n?.stop_authorized_engine_ids
  const engineColumns = useMemo<ColumnDef<ObservedEngine, unknown>[]>(
    () => [
      {
        accessorKey: "engine_id",
        header: "Engine",
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-mono text-xs font-medium">{row.original.engine_id}</span>
            <span className="text-xs text-muted-foreground">{row.original.model ?? "—"}</span>
          </div>
        ),
      },
      {
        accessorKey: "phase",
        header: "Phase",
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5">
            <StatusBadge tone={enginePhaseTone(row.original.phase)} size="sm" spinning={isTransitionalPhase(row.original.phase)}>
              {row.original.phase}
            </StatusBadge>
            {stopIds?.includes(row.original.engine_id) && (
              <StatusBadge tone="warning" size="sm" icon={CircleStop}>
                stop authorized
              </StatusBadge>
            )}
          </span>
        ),
      },
      {
        accessorKey: "endpoint",
        header: "Endpoint",
        cell: ({ getValue }) => (getValue() ? <CopyText value={getValue() as string} /> : <span className="text-muted-foreground">—</span>),
      },
      { accessorKey: "queue_depth", header: "Queue", meta: { align: "right" }, cell: ({ getValue }) => formatNumber(getValue() as number | null) },
      { accessorKey: "restart_count", header: "Restarts", meta: { align: "right" }, cell: ({ getValue }) => formatNumber((getValue() as number | undefined) ?? 0) },
      {
        id: "condition",
        header: "Condition",
        enableSorting: false,
        cell: ({ row }) => {
          const c = row.original.conditions?.[0]
          return c ? (
            <span className="text-xs">
              {c.type}={c.status}
              {c.reason && <span className="text-muted-foreground"> · {c.reason}</span>}
            </span>
          ) : (
            "—"
          )
        },
      },
      {
        accessorKey: "loaded_artifact_digest",
        header: "Artifact",
        cell: ({ getValue }) => (getValue() ? <span className="font-mono text-[11px] text-muted-foreground">{shortId((getValue() as string).replace("sha256:", ""), 12)}</span> : "—"),
      },
    ],
    [stopIds],
  )

  return (
    <ControlGate
      title={n ? n.display_name || "Unnamed host" : "Node"}
      description={
        n ? (
          <span className="flex flex-wrap items-center gap-3">
            <NodeStatusBadge status={n.status} />
            <CopyText value={n.node_id} />
            {Object.keys(n.labels).length > 0 && <TagList tags={Object.entries(n.labels).map(([k, v]) => `${k}=${v}`)} />}
          </span>
        ) : (
          nodeId
        )
      }
      actions={
        n && (
          <>
            <Button variant="outline" size="sm" onClick={() => setStopOpen(true)} disabled={n.revoked}>
              <CircleStop data-icon="inline-start" /> Authorize stop
            </Button>
            <Button size="sm" onClick={() => setPublishOpen(true)} disabled={n.revoked}>
              <Upload data-icon="inline-start" /> Publish desired state
            </Button>
            <Button variant="destructive" size="sm" onClick={() => setRevokeOpen(true)} disabled={n.revoked}>
              <Ban data-icon="inline-start" /> {n.revoked ? "Revoked" : "Revoke"}
            </Button>
          </>
        )
      }
    >
      {node.isError && !n ? (
        <ErrorState error={node.error} onRetry={() => node.refetch()} />
      ) : !n ? (
        <LoadingGrid count={4} />
      ) : (
        <>
          {n.revoked && (
            <Alert variant="destructive">
              <Ban />
              <AlertTitle>This node is revoked</AlertTitle>
              <AlertDescription>Its certificate serial is on the CRL; heartbeats, observations and desired-state fetches are rejected. Re-enroll to bring the host back.</AlertDescription>
            </Alert>
          )}
          <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
            <StatCard
              label="Lease"
              icon={Timer}
              value={n.status === "online" ? "Valid" : n.status === "offline" ? "Expired" : "—"}
              tone={n.status === "online" ? "good" : n.status === "offline" ? "serious" : "neutral"}
              hint={<LeaseText expires={n.lease_expires_at} status={n.status} />}
            />
            <StatCard
              label="Fencing epoch"
              icon={Fingerprint}
              value={n.fencing_epoch}
              hint={`incarnation ${n.incarnation} · seq ${formatNumber(n.highest_sequence)}`}
              info="Incremented on every new agent session; a stale session with an old epoch is fenced off and cannot act."
            />
            <StatCard
              label="Generation"
              icon={GitBranch}
              value={
                <>
                  {n.observed_generation ?? "—"}
                  <span className="text-base font-normal text-muted-foreground">/{n.desired_generation}</span>
                </>
              }
              tone={n.observed_generation !== null && n.observed_generation < n.desired_generation ? "warning" : "neutral"}
              hint="observed / desired"
            />
            <StatCard
              label="Certificate"
              icon={ShieldCheck}
              value={n.certificate ? formatRelative(n.certificate.not_valid_after).replace(/^in /, "") : "—"}
              hint={n.certificate ? "until expiry · agent rotates before" : "no certificate"}
              tone={n.certificate && new Date(n.certificate.not_valid_after).getTime() < now ? "critical" : "neutral"}
            />
          </div>

          <Tabs defaultValue="engines" className="gap-4">
            <TabsList>
              <TabsTrigger value="engines">
                <Cpu /> Engines ({n.engines.total})
              </TabsTrigger>
              <TabsTrigger value="capacity">Capacity</TabsTrigger>
              <TabsTrigger value="desired">
                <FileCode /> Desired state
              </TabsTrigger>
              <TabsTrigger value="identity">Identity</TabsTrigger>
              <TabsTrigger value="audit">Audit ({n.audit.length})</TabsTrigger>
            </TabsList>
            <TabsContent value="engines">
              <DataTable
                columns={engineColumns}
                data={n.observation?.engines ?? []}
                getRowId={(e) => e.engine_id}
                empty={<EmptyState bordered={false} icon={Cpu} title="No engines reported" description="Observe-only nodes report inventory without running engines." />}
              />
            </TabsContent>
            <TabsContent value="capacity" className="grid gap-4 xl:grid-cols-3">
              <Panel className="xl:col-span-2" title="Allocatable VRAM per device" description="From the agent's reservation ledger — what placement may still commit">
                <BarList
                  valueFormatter={(v) => formatBytes(v)}
                  items={Object.entries(n.observation?.capacity?.allocatable_vram_bytes ?? {})
                    .sort((a, b) => a[0].localeCompare(b[0]))
                    .map(([dev, bytes]) => ({
                      key: dev,
                      label: (
                        <span className="flex items-center gap-2">
                          <span className="font-mono text-xs">{dev}</span>
                          {n.observation?.capacity?.nvlink_groups?.[dev] && (
                            <span className="rounded border px-1 text-[10px] text-muted-foreground">NVLink {n.observation.capacity.nvlink_groups[dev]}</span>
                          )}
                        </span>
                      ),
                      value: bytes,
                    }))}
                />
              </Panel>
              <Panel title="Placement signals">
                <KeyValueGrid
                  items={[
                    { label: "Running engines", value: formatNumber(n.observation?.capacity?.running_engines ?? null) },
                    { label: "Queue depth", value: formatNumber(n.observation?.capacity?.queue_depth ?? null) },
                    { label: "Free ports", value: formatNumber(n.observation?.capacity?.free_ports ?? null) },
                    { label: "Held artifacts", value: formatNumber(n.observation?.capacity?.held_artifacts?.length ?? 0) },
                  ]}
                />
                {(n.observation?.capacity?.held_artifacts?.length ?? 0) > 0 && (
                  <ul className="mt-4 space-y-1 border-t pt-3">
                    {n.observation!.capacity!.held_artifacts!.map((a) => (
                      <li key={a} className="truncate font-mono text-[11px] text-muted-foreground" title={a}>
                        {a}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </TabsContent>
            <TabsContent value="desired">
              {n.desired ? (
                <Panel
                  title={`Generation ${n.desired.generation}`}
                  description={
                    <>
                      Published <TimeAgo value={n.desired.created_at} /> ·{" "}
                      {n.observed_generation === n.desired.generation ? "converged" : `agent at ${n.observed_generation ?? "—"}`}
                    </>
                  }
                  actions={
                    <Button size="sm" variant="outline" onClick={() => setPublishOpen(true)} disabled={n.revoked}>
                      New generation
                    </Button>
                  }
                >
                  <JsonView value={n.desired.snapshot} maxHeight={520} />
                </Panel>
              ) : (
                <EmptyState icon={FileCode} title="No desired state published" description="Until a snapshot is published the agent runs nothing in managed mode.">
                  <Button size="sm" onClick={() => setPublishOpen(true)} disabled={n.revoked}>
                    Publish generation 1
                  </Button>
                </EmptyState>
              )}
            </TabsContent>
            <TabsContent value="identity" className="grid gap-4 xl:grid-cols-2">
              <Panel title="Session & identity">
                <KeyValueGrid
                  items={[
                    { label: "Node ID", value: n.node_id, mono: true },
                    { label: "Hostname", value: n.display_name || "—" },
                    { label: "Active session", value: n.active_session_id ?? "—", mono: true },
                    { label: "Enrolled", value: <Timestamp value={n.created_at} /> },
                    { label: "Last observation", value: <TimeAgo value={n.observed_at} /> },
                    { label: <Hint content="Highest heartbeat sequence accepted; replays are rejected.">Highest sequence</Hint>, value: formatNumber(n.highest_sequence) },
                  ]}
                />
              </Panel>
              <Panel title="Certificate" description="Ed25519, issued by the meridian-control CA">
                {n.certificate ? (
                  <KeyValueGrid
                    items={[
                      { label: "Serial", value: n.certificate.serial, mono: true },
                      { label: "Valid from", value: <Timestamp value={n.certificate.not_valid_before} /> },
                      { label: "Valid until", value: <Timestamp value={n.certificate.not_valid_after} /> },
                      { label: "Status", value: n.revoked ? "Revoked (on CRL)" : "Active" },
                    ]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">No certificate on record.</p>
                )}
              </Panel>
            </TabsContent>
            <TabsContent value="audit">
              <Panel title="Node audit trail" contentClassName="px-0">
                <ol className="relative space-y-0">
                  {n.audit.map((e) => {
                    const k = AUDIT_KIND[e.kind] ?? { label: e.kind, tone: "neutral" as const }
                    return (
                      <li key={e.id} className="flex items-start gap-3 border-b px-4 py-2.5 last:border-b-0">
                        <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", { good: "bg-(--status-good)", info: "bg-(--series-1)", warning: "bg-(--status-warning)", critical: "bg-(--status-critical)", neutral: "bg-muted-foreground" }[k.tone])} />
                        <span className="min-w-0 flex-1">
                          <span className="text-sm font-medium">{k.label}</span>
                          {e.detail && <span className="ml-2 font-mono text-xs text-muted-foreground">{e.detail}</span>}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          <Timestamp value={e.created_at} />
                        </span>
                      </li>
                    )
                  })}
                  {n.audit.length === 0 && <li className="px-4 py-6 text-sm text-muted-foreground">No events.</li>}
                </ol>
              </Panel>
            </TabsContent>
          </Tabs>
          <div>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/fleet">
                <ArrowLeft data-icon="inline-start" /> All nodes
              </Link>
            </Button>
          </div>
          <PublishDesiredDialog node={n} open={publishOpen} onOpenChange={setPublishOpen} />
          <StopDialog node={n} open={stopOpen} onOpenChange={setStopOpen} />
          <ConfirmDialog
            open={revokeOpen}
            onOpenChange={setRevokeOpen}
            title={`Revoke ${n.display_name || n.node_id}?`}
            description={
              <>
                <p>The node's certificate goes on the CRL and every heartbeat, observation and desired-state call is rejected immediately. Its engines drop out of the serving projection, so the gateway stops routing to them.</p>
                <p>This cannot be undone — the host must re-enroll with a new token.</p>
              </>
            }
            confirmLabel="Revoke node"
            destructive
            typeToConfirm={n.display_name || n.node_id}
            pending={revoke.isPending}
            onConfirm={() => revoke.mutate()}
          />
        </>
      )}
    </ControlGate>
  )
}
