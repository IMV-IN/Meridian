import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, CheckCheck, Copy, ShieldCheck, Ticket, X } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { CodeBlock, useCopy } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, TimeAgo } from "@/components/misc"
import { Panel } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { qk, useClaims, useControl, useTokens } from "@/hooks/queries"
import type { Claim, EnrollmentTokenView } from "@/lib/api/types"
import { shortId } from "@/lib/format"
import { cn } from "@/lib/utils"

import { ControlGate } from "./shared"

const TTLS = [
  { s: 900, label: "15 minutes" },
  { s: 3600, label: "1 hour" },
  { s: 86_400, label: "24 hours" },
  { s: 604_800, label: "7 days" },
]

const STEPS = [
  { t: "Mint", d: "One-time token, hashed at rest" },
  { t: "Enroll", d: "Agent presents token + its Ed25519 public key" },
  { t: "Approve", d: "Auto, or an operator approves the claim here" },
  { t: "Prove", d: "Agent signs a nonce to prove key possession" },
  { t: "Certify", d: "CA issues the node certificate; session + lease begin" },
]

function MintPanel({ autoOpen }: { autoOpen: boolean }) {
  const { api, baseUrl } = useControl()
  const qc = useQueryClient()
  const [auto, setAuto] = useState(false)
  const [ttl, setTtl] = useState("3600")
  const [token, setToken] = useState<string | null>(null)
  const { copied, copy } = useCopy(2500)
  const mint = useMutation({
    mutationFn: () => api!.mintToken({ auto_approve: auto, ttl_seconds: Number(ttl) }),
    onSuccess: (r) => {
      setToken(r.token)
      void qc.invalidateQueries({ queryKey: qk.control(baseUrl) })
    },
    onError: (e) => toast.error("Could not mint token", { description: e instanceof Error ? e.message : String(e) }),
  })
  return (
    <Panel
      title="Mint a join token"
      description="Single use. Hand it to whoever installs the agent on the GPU host — it is shown only once."
      className={cn(autoOpen && !token && "ring-2 ring-ring/40")}
    >
      {token ? (
        <div className="min-w-0 space-y-4">
          <Alert>
            <ShieldCheck />
            <AlertTitle>Token minted · {auto ? "auto-approves" : "requires approval"}</AlertTitle>
            <AlertDescription>Copy it now — meridian-control stores only its hash.</AlertDescription>
          </Alert>
          <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-2 pl-3">
            <code className="min-w-0 flex-1 font-mono text-[13px] break-all select-all">{token}</code>
            <Button size="sm" variant={copied ? "secondary" : "default"} onClick={() => void copy(token, "Join token")}>
              {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <div className="space-y-1.5">
            <div className="text-sm font-medium">On the GPU host</div>
            <CodeBlock
              language="shell"
              code={`export MERIDIAN_NODE_TOKEN='<paste token>'
# config: control_plane_url: ${baseUrl}
meridian-node --config /etc/meridian-node/config.yaml check-config
meridian-node --config /etc/meridian-node/config.yaml run`}
            />
            <p className="text-xs text-muted-foreground">Start in observe-only mode before enabling managed-host.</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setToken(null)}>
            Mint another
          </Button>
        </div>
      ) : (
        <FieldGroup className="gap-5">
          <Field orientation="horizontal" className="items-start justify-between rounded-lg border p-3">
            <div>
              <FieldLabel htmlFor="mint-auto">Auto-approve</FieldLabel>
              <FieldDescription>Skip the approval queue. Use only for tokens delivered over a trusted channel.</FieldDescription>
            </div>
            <Switch id="mint-auto" checked={auto} onCheckedChange={setAuto} />
          </Field>
          <Field>
            <FieldLabel htmlFor="mint-ttl">Expires after</FieldLabel>
            <Select value={ttl} onValueChange={setTtl}>
              <SelectTrigger id="mint-ttl" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TTLS.map((t) => (
                  <SelectItem key={t.s} value={String(t.s)}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Button onClick={() => mint.mutate()} disabled={mint.isPending} className="w-fit">
            {mint.isPending ? <Spinner data-icon="inline-start" /> : <Ticket data-icon="inline-start" />}
            Mint token
          </Button>
        </FieldGroup>
      )}
    </Panel>
  )
}

function PendingClaims() {
  const { api, baseUrl } = useControl()
  const qc = useQueryClient()
  const pending = useClaims("pending")
  const [denying, setDenying] = useState<Claim | null>(null)
  const act = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) => (approve ? api!.approveClaim(id) : api!.denyClaim(id)),
    onSuccess: (_r, v) => {
      toast.success(v.approve ? "Claim approved" : "Claim denied", {
        description: v.approve ? "The agent receives its certificate on its next poll." : "The agent will be told enrollment was denied.",
      })
      setDenying(null)
      void qc.invalidateQueries({ queryKey: qk.control(baseUrl) })
    },
    onError: (e) => toast.error("Action failed", { description: e instanceof Error ? e.message : String(e) }),
  })
  const claims = pending.data ?? []
  return (
    <Panel
      title="Pending approval"
      description="Nodes that enrolled with a non-auto token. Verify the node ID with whoever installed it before approving."
      actions={claims.length > 0 && <StatusBadge tone="warning" size="sm">{claims.length} waiting</StatusBadge>}
      contentClassName="px-0"
    >
      {claims.length === 0 ? (
        <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
          <CheckCheck className="size-4" /> Nothing to approve.
        </div>
      ) : (
        <ul className="divide-y">
          {claims.map((c) => (
            <li key={c.claim_id} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <div className="font-mono text-xs font-medium">{c.node_id}</div>
                <div className="text-xs text-muted-foreground">
                  claim <span className="font-mono">{c.claim_id}</span> · <TimeAgo value={c.created_at} />
                </div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => setDenying(c)} disabled={act.isPending}>
                  <X data-icon="inline-start" /> Deny
                </Button>
                <Button size="sm" onClick={() => act.mutate({ id: c.claim_id, approve: true })} disabled={act.isPending}>
                  <Check data-icon="inline-start" /> Approve
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={!!denying}
        onOpenChange={(o) => !o && setDenying(null)}
        title="Deny this enrollment?"
        description={<p>The agent for {denying?.node_id} is refused permanently; it must enroll again with a new token.</p>}
        confirmLabel="Deny claim"
        destructive
        pending={act.isPending}
        onConfirm={() => {
          if (denying) act.mutate({ id: denying.claim_id, approve: false })
        }}
      />
    </Panel>
  )
}

const claimColumns: ColumnDef<Claim, unknown>[] = [
  { accessorKey: "node_id", header: "Node", cell: ({ getValue }) => <span className="font-mono text-xs" title={getValue() as string}>{shortId(getValue() as string, 18)}</span> },
  { accessorKey: "claim_id", header: "Claim", cell: ({ getValue }) => <span className="font-mono text-xs text-muted-foreground" title={getValue() as string}>{shortId(getValue() as string, 16)}</span> },
  {
    accessorKey: "status",
    header: "Decision",
    cell: ({ getValue }) => {
      const s = getValue() as Claim["status"]
      return (
        <StatusBadge tone={s === "approved" ? "good" : s === "denied" ? "critical" : "warning"} size="sm">
          {s}
        </StatusBadge>
      )
    },
  },
  { accessorKey: "created_at", header: "Requested", cell: ({ getValue }) => <TimeAgo value={getValue() as string} className="text-xs" /> },
]

const tokenColumns: ColumnDef<EnrollmentTokenView, unknown>[] = [
  { accessorKey: "token_ref", header: "Token ref", cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}…</span> },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ getValue }) => {
      const s = getValue() as EnrollmentTokenView["status"]
      return (
        <StatusBadge tone={s === "active" ? "info" : s === "used" ? "good" : "muted"} size="sm">
          {s}
        </StatusBadge>
      )
    },
  },
  { accessorKey: "auto_approve", header: "Approval", cell: ({ getValue }) => <span className="text-xs">{getValue() ? "automatic" : "manual"}</span> },
  { accessorKey: "expires_at", header: "Expires", cell: ({ getValue }) => <TimeAgo value={getValue() as string | null} className="text-xs" /> },
]

export default function EnrollmentPage() {
  const [params] = useSearchParams()
  const claims = useClaims()
  const tokens = useTokens()
  return (
    <ControlGate title="Enrollment" description="How GPU hosts join the fleet: one-time tokens, an approval gate, proof of key possession, then a CA-issued certificate.">
      <ol className="grid gap-2 sm:grid-cols-5">
        {STEPS.map((s, i) => (
          <li key={s.t} className="rounded-lg border bg-card p-3">
            <div className="flex items-center gap-2">
              <span className="flex size-5 items-center justify-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground">{i + 1}</span>
              <span className="text-sm font-medium">{s.t}</span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{s.d}</p>
          </li>
        ))}
      </ol>
      <div className="grid gap-4 xl:grid-cols-2">
        <MintPanel autoOpen={params.get("mint") === "1"} />
        <PendingClaims />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Claim history">
          <DataTable columns={claimColumns} data={claims.data} loading={claims.isLoading} dense pageSize={10} getRowId={(c) => c.claim_id} empty={<EmptyState bordered={false} icon={Ticket} title="No claims yet" />} />
        </Panel>
        <Panel title="Join tokens" description="Referenced by hash prefix — raw tokens are never stored">
          <DataTable columns={tokenColumns} data={tokens.data} loading={tokens.isLoading} dense pageSize={10} getRowId={(t) => t.token_ref} empty={<EmptyState bordered={false} icon={Ticket} title="No tokens minted" />} />
        </Panel>
      </div>
    </ControlGate>
  )
}
