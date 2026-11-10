import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, Network, Plus, Radio } from "lucide-react"
import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router"

import { CodeBlock, useCopy } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { TimeAgo } from "@/components/misc"
import { Page, PageHeader } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState, ErrorState } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { ApiError } from "@/lib/api/client"
import { formatMs, formatNumber, titleCase } from "@/lib/format"

import { atLeast, cloud, type GatewaySummary } from "../api"
import { GatewayStatusBadge } from "../components"
import { useGateways } from "../hooks"
import { useCloud, useWorkspace } from "../session"

function ConnectGatewayDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-2xl">
        {/* Radix mounts content only while open and keeps it through the exit
            animation, so the form resets on every open without flashing an
            empty dialog on close. */}
        <ConnectBody onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function ConnectBody({ onClose }: { onClose: () => void }) {
  const ws = useWorkspace()
  const { info } = useCloud()
  const qc = useQueryClient()
  const gateways = useGateways()
  const [name, setName] = useState("")
  const [token, setToken] = useState<string | null>(null)
  // Gateways that existed when the token was minted; a new id after that
  // means "connected". (Snapshotting at open is wrong when the dialog is
  // opened from a link before the list has loaded.)
  const [baseline, setBaseline] = useState<Set<string> | null>(null)
  const { copied, copy } = useCopy(2500)
  const create = useMutation({
    mutationFn: () => cloud.createGatewayToken(ws.id, name.trim() || "gateway"),
    onSuccess: async (r) => {
      const current = await qc.fetchQuery({ queryKey: ["cloud", "gateways", ws.id], queryFn: ({ signal }) => cloud.gateways(ws.id, signal) })
      setBaseline(new Set(current.map((g) => g.gateway_id)))
      setToken(r.token)
      void qc.invalidateQueries({ queryKey: ["cloud", "gateway-tokens", ws.id] })
    },
  })
  const connected = useMemo(() => (baseline ? (gateways.data ?? []).find((g) => !baseline.has(g.gateway_id)) : undefined), [gateways.data, baseline])
  const gwName = (name.trim() || "gateway").replace(/[^A-Za-z0-9._-]+/g, "-")
  const yaml = `# Add to your gateway's config.yaml
control_plane:
  enabled: true
  url: ${info.public_url}
  gateway_name: ${gwName}
  # token is read from MERIDIAN_CLOUD_TOKEN — keep it out of this file`
  const install = `curl -fsSL ${info.public_url}/install.sh | bash -s -- \\
  --cloud ${info.public_url} --gateway-name ${gwName}`

  return (
    <>
        <DialogHeader>
          <DialogTitle>Connect a gateway</DialogTitle>
          <DialogDescription>
            The gateway keeps serving traffic inside your network. It connects outbound to pull the engines this workspace may route to and to
            report health. Prompts never leave your network.
          </DialogDescription>
        </DialogHeader>
        {!token ? (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              create.mutate()
            }}
          >
            <Field data-invalid={create.isError}>
              <FieldLabel htmlFor="gw-name">Gateway name</FieldLabel>
              <Input id="gw-name" name="gateway-name" autoComplete="off" spellCheck={false} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. prod-mumbai-1" maxLength={100} />
              {create.error ? (
                <FieldError>{create.error instanceof ApiError ? create.error.message : "Failed"}</FieldError>
              ) : (
                <FieldDescription>Shown in this console. One token can serve several gateway replicas.</FieldDescription>
              )}
            </Field>
          </form>
        ) : (
          <div className="min-w-0 space-y-5">
            <div className="space-y-2">
              <div className="text-sm font-medium">1. Copy the gateway token — shown once</div>
              <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-2 pl-3">
                <code className="min-w-0 flex-1 font-mono text-[12.5px] break-all select-all">{token}</code>
                <Button size="sm" variant={copied ? "secondary" : "default"} onClick={() => void copy(token, "Gateway token")}>
                  {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              <div className="text-sm font-medium">2. On a new machine, run this and paste the token when it asks</div>
              <CodeBlock language="shell" code={install} />
              <p className="text-sm text-muted-foreground">
                Installs the gateway into ~/.meridian and starts it.{" "}
                <a href="/install" target="_blank" rel="noopener noreferrer" className="text-foreground underline underline-offset-4">
                  Choose routing, keys and limits
                </a>{" "}
                for more options.
              </p>
            </div>
            <div className="space-y-2">
              <div className="text-sm font-medium">Already running a gateway? Add this to its config.yaml</div>
              <CodeBlock language="yaml" code={yaml} />
              <p className="text-sm text-muted-foreground">
                Then restart it with <code className="font-mono text-[0.85em]">MERIDIAN_CLOUD_TOKEN</code> set to the token.
              </p>
            </div>
            {connected ? (
              <Alert>
                <Radio />
                <AlertTitle>{connected.name} is connected</AlertTitle>
                <AlertDescription>First report received. You can close this dialog.</AlertDescription>
              </Alert>
            ) : (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner /> Waiting for the first report…
              </p>
            )}
          </div>
        )}
        <DialogFooter>
          {!token ? (
            <>
              <Button variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button onClick={() => create.mutate()} disabled={create.isPending}>
                {create.isPending && <Spinner data-icon="inline-start" />}
                Create token
              </Button>
            </>
          ) : (
            <Button onClick={onClose}>{connected ? "Done" : "Close"}</Button>
          )}
        </DialogFooter>
    </>
  )
}

const columns: ColumnDef<GatewaySummary, unknown>[] = [
  {
    accessorKey: "name",
    header: "Gateway",
    cell: ({ row }) => (
      <div className="flex flex-col">
        <span className="font-medium">{row.original.name}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{row.original.gateway_id}</span>
      </div>
    ),
  },
  { id: "status", accessorFn: (g) => (g.online ? 1 : 0), header: "Status", cell: ({ row }) => <GatewayStatusBadge g={row.original} /> },
  { accessorKey: "version", header: "Version", cell: ({ getValue }) => <span className="font-mono text-xs">v{getValue() as string}</span> },
  { accessorKey: "strategy", header: "Strategy", cell: ({ getValue }) => (getValue() ? titleCase(getValue() as string) : "—") },
  {
    id: "backends",
    accessorFn: (g) => g.backends_healthy,
    header: "Backends",
    meta: { align: "right" },
    cell: ({ row }) => `${row.original.backends_healthy}/${row.original.backends_total}`,
  },
  { id: "rps", accessorFn: (g) => g.window.rps ?? 0, header: "req/s", meta: { align: "right" }, cell: ({ getValue }) => formatNumber(getValue() as number, { digits: 1 }) },
  { id: "p95", accessorFn: (g) => g.window.p95_ms ?? 0, header: "p95", meta: { align: "right" }, cell: ({ row }) => formatMs(row.original.window.p95_ms ?? null) },
  {
    id: "canary",
    header: "Canary",
    enableSorting: false,
    cell: ({ row }) => {
      const c = row.original.canary
      if (!c) return <span className="text-muted-foreground">—</span>
      return (
        <StatusBadge tone={c.rolled_back ? "critical" : "info"} size="sm" icon={null}>
          {c.rolled_back ? "rolled back" : `${c.weight}%`}
        </StatusBadge>
      )
    },
  },
  { accessorKey: "last_seen_at", header: "Last report", cell: ({ getValue }) => <TimeAgo value={getValue() as string} className="text-xs" /> },
]

export default function GatewaysPage() {
  const ws = useWorkspace()
  const gateways = useGateways()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const connectOpen = params.get("connect") === "1"
  const setConnect = (o: boolean) => setParams(o ? { connect: "1" } : {}, { replace: true })
  const data = gateways.data ?? []
  const canAdmin = atLeast(ws.role, "admin")

  return (
    <Page>
      <PageHeader
        title="Gateways"
        description="Meridian gateways running in your network. Each sends a metadata-only health report every 15 seconds."
        actions={
          canAdmin && (
            <Button size="sm" onClick={() => setConnect(true)}>
              <Plus data-icon="inline-start" /> Connect gateway
            </Button>
          )
        }
      />
      {gateways.isError && !gateways.data ? (
        <ErrorState error={gateways.error} onRetry={() => gateways.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={data}
          loading={gateways.isLoading}
          getRowId={(g) => g.gateway_id}
          onRowClick={(g) => navigate(`/gateways/${encodeURIComponent(g.gateway_id)}`)}
          empty={
            <EmptyState bordered={false} icon={Network} title="No gateways connected" description="Connect a gateway to see its backends, traffic and canary state here.">
              {canAdmin && (
                <Button size="sm" onClick={() => setConnect(true)}>
                  Connect gateway
                </Button>
              )}
            </EmptyState>
          }
        />
      )}
      {canAdmin && <ConnectGatewayDialog open={connectOpen} onOpenChange={setConnect} />}
    </Page>
  )
}
