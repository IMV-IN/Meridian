import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Coins, Copy, Eye, KeyRound, Lock, MoreHorizontal, Plus, ShieldCheck, Trash2, TriangleAlert, Wrench, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { useNavigate, useSearchParams } from "react-router"
import { toast } from "sonner"
import { z } from "zod"

import { CodeBlock, useCopy } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, SearchInput } from "@/components/misc"
import { Page, PageHeader, Panel, TagList } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge } from "@/components/status-badge"
import { EmptyState, ErrorState, FeatureOffState } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { qk, useConfig, useKeys, useModels } from "@/hooks/queries"
import { ApiError } from "@/lib/api/client"
import { gateway, gatewayOrigin } from "@/lib/api/gateway"
import type { ApiKeyView, Capabilities, KeyCreateResult, Role } from "@/lib/api/types"
import { capabilitiesFor } from "@/lib/rbac"
import { cn } from "@/lib/utils"
import { useAuth } from "@/providers/auth-provider"
import { useUrlState } from "@/hooks/use-url-state"

const CAP_ROWS: { cap: keyof Capabilities; label: string; detail: string }[] = [
  { cap: "view_ops", label: "Operator views", detail: "status, requests, config, budgets, console" },
  { cap: "reload", label: "Config reload", detail: "POST /meridian/reload" },
  { cap: "manage_keys", label: "Key management", detail: "create & delete API keys" },
  { cap: "read_all_cost", label: "All-org cost", detail: "usage for every org/team" },
]

const ROLE_ICON: Record<Role, typeof Eye> = { viewer: Eye, operator: Wrench, admin: ShieldCheck }

function RoleBadge({ k }: { k: Pick<ApiKeyView, "role" | "cost_admin" | "ops_admin"> }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {k.role ? (
        <StatusBadge tone="neutral" size="sm" icon={ROLE_ICON[k.role]}>
          {k.role}
        </StatusBadge>
      ) : (
        <span className="text-xs text-muted-foreground">tenant</span>
      )}
      {k.ops_admin && (
        <Badge variant="outline" className="h-5 text-[10px]">
          ops_admin
        </Badge>
      )}
      {k.cost_admin && (
        <Badge variant="outline" className="h-5 text-[10px]">
          cost_admin
        </Badge>
      )}
    </span>
  )
}

function CapabilityMatrix() {
  const roles: { label: string; k: { role: Role | null; cost_admin: boolean; ops_admin: boolean } }[] = [
    { label: "Tenant", k: { role: null, cost_admin: false, ops_admin: false } },
    { label: "Viewer", k: { role: "viewer", cost_admin: false, ops_admin: false } },
    { label: "Operator", k: { role: "operator", cost_admin: false, ops_admin: false } },
    { label: "Admin", k: { role: "admin", cost_admin: false, ops_admin: false } },
    { label: "Finance", k: { role: null, cost_admin: true, ops_admin: false } },
  ]
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-4 text-xs">Right</TableHead>
          {roles.map((r) => (
            <TableHead key={r.label} className="text-center text-xs">
              {r.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {CAP_ROWS.map((row) => (
          <TableRow key={row.cap}>
            <TableCell className="pl-4">
              <div className="text-[13px] font-medium">{row.label}</div>
              <div className="text-xs text-muted-foreground">{row.detail}</div>
            </TableCell>
            {roles.map((r) => (
              <TableCell key={r.label} className="text-center">
                {capabilitiesFor(r.k)[row.cap] ? (
                  <Check className="mx-auto size-4 text-good-ink" aria-label="allowed" />
                ) : (
                  <X className="mx-auto size-3.5 text-muted-foreground/50" aria-label="not allowed" />
                )}
              </TableCell>
            ))}
          </TableRow>
        ))}
        <TableRow>
          <TableCell className="pl-4">
            <div className="text-[13px] font-medium">Inference</div>
            <div className="text-xs text-muted-foreground">/v1/* within allowed models</div>
          </TableCell>
          {roles.map((r) => (
            <TableCell key={r.label} className="text-center">
              <Check className="mx-auto size-4 text-good-ink" aria-label="allowed" />
            </TableCell>
          ))}
        </TableRow>
      </TableBody>
    </Table>
  )
}

// ── Create ────────────────────────────────────────────────────────────────

const ID = /^[A-Za-z0-9._:@/-]+$/
const createSchema = z.object({
  org_id: z.string().trim().min(1, "Every key belongs to an org").regex(ID, "Letters, digits and . _ : @ / - only"),
  team_id: z.string().trim().regex(ID, "Invalid characters").or(z.literal("")),
  user_id: z.string().trim().regex(ID, "Invalid characters").or(z.literal("")),
  key_id: z.string().trim().max(64).regex(ID, "Invalid characters").or(z.literal("")),
  role: z.enum(["none", "viewer", "operator", "admin"]),
  cost_admin: z.boolean(),
  ops_admin: z.boolean(),
  allowed_models: z.array(z.string()),
  pii_policy: z.enum(["inherit", "block", "redact_and_replace", "redact_for_logs", "audit_only"]),
})
type CreateValues = z.infer<typeof createSchema>

const DEFAULTS: CreateValues = {
  org_id: "",
  team_id: "",
  user_id: "",
  key_id: "",
  role: "none",
  cost_admin: false,
  ops_admin: false,
  allowed_models: [],
  pii_policy: "inherit",
}

function CreateKeyDialog({
  open,
  onOpenChange,
  orgs,
  onCreated,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  orgs: string[]
  onCreated: (r: KeyCreateResult) => void
}) {
  const qc = useQueryClient()
  const models = useModels()
  const config = useConfig()
  const modelIds = (models.data?.data ?? []).map((m) => m.id)
  const [customModel, setCustomModel] = useState("")
  const form = useForm<CreateValues>({ resolver: zodResolver(createSchema), defaultValues: DEFAULTS })
  const v = form.watch()
  const caps = capabilitiesFor({ role: v.role === "none" ? null : v.role, cost_admin: v.cost_admin, ops_admin: v.ops_admin })

  useEffect(() => {
    if (!open) {
      form.reset(DEFAULTS)
      setCustomModel("")
    }
  }, [open, form])

  const create = useMutation({
    mutationFn: (values: CreateValues) =>
      gateway.createKey({
        org_id: values.org_id,
        team_id: values.team_id || null,
        user_id: values.user_id || null,
        key_id: values.key_id || null,
        role: values.role === "none" ? null : values.role,
        cost_admin: values.cost_admin,
        ops_admin: values.ops_admin,
        allowed_models: values.allowed_models,
        pii_policy: values.pii_policy === "inherit" ? null : values.pii_policy,
      }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: qk.keys })
      onOpenChange(false)
      onCreated(r)
    },
  })

  const toggleModel = (m: string) => {
    const cur = form.getValues("allowed_models")
    form.setValue("allowed_models", cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m])
  }
  const errors = form.formState.errors
  const err = create.error instanceof ApiError ? create.error : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92svh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="border-b px-6 pt-6 pb-4">
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>
            Written to <code className="text-xs">auth.keys_file</code> and active immediately. The secret is shown once.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit((values) => create.mutate(values))}
          className="scrollbar-thin flex min-h-0 flex-1 flex-col overflow-y-auto"
          noValidate
        >
          <div className="grid gap-6 px-6 py-5 md:grid-cols-[1fr_220px]">
            <FieldGroup className="gap-5">
              <FieldSet>
                <FieldLegend className="text-sm">Identity</FieldLegend>
                <FieldDescription className="-mt-1 text-xs">Requests made with this key are attributed, budgeted and isolated by org → team → user.</FieldDescription>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field data-invalid={!!errors.org_id} className="sm:col-span-2">
                    <FieldLabel htmlFor="k-org">Org *</FieldLabel>
                    <Input id="k-org" list="k-org-list" placeholder="e.g. acme" autoComplete="off" spellCheck={false} aria-invalid={!!errors.org_id} {...form.register("org_id")} />
                    <datalist id="k-org-list">
                      {orgs.map((o) => (
                        <option key={o} value={o} />
                      ))}
                    </datalist>
                    {errors.org_id && <FieldError>{errors.org_id.message}</FieldError>}
                  </Field>
                  <Field data-invalid={!!errors.team_id}>
                    <FieldLabel htmlFor="k-team">Team</FieldLabel>
                    <Input id="k-team" placeholder="e.g. ml-platform" autoComplete="off" spellCheck={false} {...form.register("team_id")} />
                    {errors.team_id && <FieldError>{errors.team_id.message}</FieldError>}
                  </Field>
                  <Field data-invalid={!!errors.user_id}>
                    <FieldLabel htmlFor="k-user">User</FieldLabel>
                    <Input id="k-user" placeholder="e.g. dana" autoComplete="off" spellCheck={false} {...form.register("user_id")} />
                  </Field>
                  <Field data-invalid={!!errors.key_id} className="sm:col-span-2">
                    <FieldLabel htmlFor="k-id">Key ID</FieldLabel>
                    <Input id="k-id" placeholder="Generated if blank" autoComplete="off" spellCheck={false} className="font-mono text-xs" {...form.register("key_id")} />
                    {errors.key_id && <FieldError>{errors.key_id.message}</FieldError>}
                  </Field>
                </div>
              </FieldSet>
              <FieldSet>
                <FieldLegend className="text-sm">Permissions</FieldLegend>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field>
                    <FieldLabel htmlFor="k-role">Role</FieldLabel>
                    <Select value={v.role} onValueChange={(r) => form.setValue("role", r as CreateValues["role"])}>
                      <SelectTrigger id="k-role" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">None — tenant / application key</SelectItem>
                        <SelectItem value="viewer">Viewer — read operator views</SelectItem>
                        <SelectItem value="operator">Operator — views + reload</SelectItem>
                        <SelectItem value="admin">Admin — everything incl. keys</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="k-pii">PII policy</FieldLabel>
                    <Select value={v.pii_policy} onValueChange={(p) => form.setValue("pii_policy", p as CreateValues["pii_policy"])}>
                      <SelectTrigger id="k-pii" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inherit">Inherit gateway ({config.data?.pii.policy ?? "global"})</SelectItem>
                        <SelectItem value="block">Block</SelectItem>
                        <SelectItem value="redact_and_replace">Redact and replace</SelectItem>
                        <SelectItem value="redact_for_logs">Redact for logs</SelectItem>
                        <SelectItem value="audit_only">Audit only</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <Field orientation="horizontal" className="items-start justify-between rounded-lg border p-3">
                    <div>
                      <FieldLabel htmlFor="k-cost">cost_admin</FieldLabel>
                      <FieldDescription className="text-xs">Read usage &amp; cost for every org (finance)</FieldDescription>
                    </div>
                    <Switch id="k-cost" checked={v.cost_admin} onCheckedChange={(c) => form.setValue("cost_admin", c)} />
                  </Field>
                  <Field orientation="horizontal" className="items-start justify-between rounded-lg border p-3">
                    <div>
                      <FieldLabel htmlFor="k-ops">ops_admin</FieldLabel>
                      <FieldDescription className="text-xs">Legacy flag: reload + key management</FieldDescription>
                    </div>
                    <Switch id="k-ops" checked={v.ops_admin} onCheckedChange={(c) => form.setValue("ops_admin", c)} />
                  </Field>
                </div>
              </FieldSet>
              <FieldSet>
                <FieldLegend className="text-sm">Model allow-list</FieldLegend>
                <FieldDescription className="-mt-1 text-xs">Leave empty to allow every model. Requests for other models get 403.</FieldDescription>
                <div className="flex flex-wrap gap-1.5">
                  {[...new Set([...modelIds, ...v.allowed_models])].map((m) => {
                    const on = v.allowed_models.includes(m)
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => toggleModel(m)}
                        aria-pressed={on}
                        className={cn(
                          "inline-flex h-7 items-center gap-1.5 rounded-md border px-2 font-mono text-xs transition-colors",
                          on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                        )}
                      >
                        {on && <Check className="size-3" />}
                        {m}
                      </button>
                    )
                  })}
                  <span className="inline-flex items-center gap-1">
                    <Input
                      name="custom-model"
                      autoComplete="off"
                      spellCheck={false}
                      aria-label="Add another model ID"
                      value={customModel}
                      onChange={(e) => setCustomModel(e.target.value)}
                      placeholder="Other model ID…"
                      className="h-7 w-44 font-mono text-xs"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault()
                          if (customModel.trim()) toggleModel(customModel.trim())
                          setCustomModel("")
                        }
                      }}
                    />
                  </span>
                </div>
              </FieldSet>
            </FieldGroup>
            <aside className="space-y-3 rounded-xl border bg-muted/40 p-4 text-sm md:self-start">
              <div className="font-medium">Effective rights</div>
              <ul className="space-y-2">
                {CAP_ROWS.map((row) => (
                  <li key={row.cap} className="flex items-start gap-2">
                    {caps[row.cap] ? <Check className="mt-0.5 size-3.5 shrink-0 text-good-ink" /> : <X className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60" />}
                    <span className={cn(!caps[row.cap] && "text-muted-foreground")}>{row.label}</span>
                  </li>
                ))}
              </ul>
              <p className="border-t pt-3 text-xs text-muted-foreground">
                {v.allowed_models.length ? `Inference limited to ${v.allowed_models.length} model${v.allowed_models.length === 1 ? "" : "s"}.` : "Inference allowed for all models."}
              </p>
            </aside>
          </div>
          {err && (
            <div className="px-6 pb-4">
              <Alert variant="destructive">
                <TriangleAlert />
                <AlertTitle>Could not create key</AlertTitle>
                <AlertDescription>{err.message}</AlertDescription>
              </Alert>
            </div>
          )}
          <DialogFooter className="sticky bottom-0 mt-auto border-t bg-background px-6 py-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Spinner data-icon="inline-start" /> : <KeyRound data-icon="inline-start" />}
              Create key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RevealKeyDialog({ result, onClose }: { result: KeyCreateResult | null; onClose: () => void }) {
  const { copied, copy } = useCopy(2500)
  const [ack, setAck] = useState(false)
  return (
    <Dialog open={!!result} onOpenChange={(o) => !o && ack && onClose()}>
      <DialogContent className="sm:max-w-lg" showCloseButton={false} onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="size-5 text-good-ink" /> Key created
          </DialogTitle>
          <DialogDescription>
            Copy the secret now. Meridian stores it only in the keys file and will never display it again — everywhere else it is
            identified as <span className="font-mono">{result?.key_id}</span>.
          </DialogDescription>
        </DialogHeader>
        {result && (
          <div className="min-w-0 space-y-4">
            <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-2 pl-3">
              <code className="min-w-0 flex-1 font-mono text-[13px] break-all select-all">{result.key}</code>
              <Button size="sm" variant={copied ? "secondary" : "default"} onClick={() => void copy(result.key, "API key")}>
                {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <CodeBlock
              language="shell"
              code={`export MERIDIAN_API_KEY='<paste key>'
curl ${gatewayOrigin()}/v1/models -H "Authorization: Bearer $MERIDIAN_API_KEY"`}
            />
            <label className="flex items-start gap-2.5 text-sm">
              <Checkbox checked={ack} onCheckedChange={(c) => setAck(c === true)} className="mt-0.5" />
              I've stored this key in a secret manager.
            </label>
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose} disabled={!ack}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function KeysPage() {
  const keys = useKeys()
  const { features, identity, authEnabled } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useUrlState("q")
  const [orgF, setOrgF] = useUrlState("org", "all")
  const [roleF, setRoleF] = useUrlState("role", "all")
  const [created, setCreated] = useState<KeyCreateResult | null>(null)
  const [victim, setVictim] = useState<ApiKeyView | null>(null)
  const createOpen = params.get("create") === "1"
  const setCreateOpen = (o: boolean) => {
    const next = new URLSearchParams(params)
    if (o) next.set("create", "1")
    else next.delete("create")
    setParams(next, { replace: true })
  }

  const del = useMutation({
    mutationFn: (keyId: string) => gateway.deleteKey(keyId),
    onSuccess: (r) => {
      toast.success(`Key ${r.key_id} deleted`, { description: "It stopped authenticating immediately." })
      setVictim(null)
      void qc.invalidateQueries({ queryKey: qk.keys })
    },
    onError: (e) => toast.error("Delete refused", { description: e instanceof Error ? e.message : String(e) }),
  })

  const data = useMemo(() => keys.data ?? [], [keys.data])
  const orgs = useMemo(() => [...new Set(data.map((k) => k.org_id))].sort(), [data])
  const filtered = useMemo(
    () =>
      data.filter(
        (k) =>
          (orgF === "all" || k.org_id === orgF) &&
          (roleF === "all" || (roleF === "tenant" ? !k.role && !k.cost_admin && !k.ops_admin : roleF === "finance" ? k.cost_admin : k.role === roleF)),
      ),
    [data, orgF, roleF],
  )
  const isInline = (k: ApiKeyView) => k.source === "inline"
  const canCreate = features.key_lifecycle

  const columns = useMemo<ColumnDef<ApiKeyView, unknown>[]>(
    () => [
      {
        accessorKey: "key_id",
        header: "Key",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-mono text-[12.5px] font-medium">{row.original.key_id}</span>
            {row.original.key_id === identity?.key_id && (
              <Badge variant="secondary" className="h-5 text-[10px]">
                you
              </Badge>
            )}
          </div>
        ),
      },
      {
        id: "identity",
        accessorFn: (k) => [k.org_id, k.team_id, k.user_id].filter(Boolean).join(" / "),
        header: "Org / team / user",
        cell: ({ row }) => (
          <span>
            <span className="font-medium">{row.original.org_id}</span>
            {row.original.team_id && <span className="text-muted-foreground"> / {row.original.team_id}</span>}
            {row.original.user_id && <span className="text-muted-foreground"> / {row.original.user_id}</span>}
          </span>
        ),
      },
      {
        id: "role",
        accessorFn: (k) => k.role ?? (k.cost_admin ? "finance" : "tenant"),
        header: "Role",
        cell: ({ row }) => <RoleBadge k={row.original} />,
      },
      {
        accessorKey: "allowed_models",
        header: "Models",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.allowed_models.length ? <TagList tags={row.original.allowed_models} max={3} /> : <span className="text-xs text-muted-foreground">all models</span>,
      },
      {
        accessorKey: "source",
        header: "Source",
        cell: ({ row }) =>
          isInline(row.original) ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Defined in the main config file">
              <Lock className="size-3" /> config (read-only)
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">keys file</span>
          ),
      },
      {
        id: "actions",
        header: () => <span className="sr-only">Actions</span>,
        enableSorting: false,
        meta: { align: "right" },
        cell: ({ row }) => {
          const k = row.original
          const self = k.key_id === identity?.key_id
          return (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${k.key_id}`} onClick={(e) => e.stopPropagation()}>
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                <DropdownMenuItem onClick={() => navigate(`/usage?key=${encodeURIComponent(k.key_id)}&org=${encodeURIComponent(k.org_id)}`)}>
                  <Coins /> View usage
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => {
                    void navigator.clipboard?.writeText(k.key_id)
                    toast.success("Key ID copied")
                  }}
                >
                  <Copy /> Copy key ID
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" disabled={isInline(k) || self || !canCreate} onClick={() => setVictim(k)}>
                  <Trash2 /> {isInline(k) ? "Delete (edit config)" : self ? "Delete (your own key)" : "Delete key"}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )
        },
      },
    ],
    [identity, navigate, canCreate],
  )

  if (!authEnabled)
    return (
      <Page>
        <PageHeader title="API keys" />
        <FeatureOffState
          icon={KeyRound}
          title="Authentication is disabled"
          description="Key management needs API-key auth. Enable it with a durable keys file, then reload or restart the gateway."
          snippet={`auth:\n  enabled: true\n  keys_file: /etc/meridian/keys.yaml`}
        />
      </Page>
    )

  return (
    <Page>
      <PageHeader
        title="API keys"
        description="Keys map callers to an org → team → user identity that drives attribution, budgets, model access and isolation. The raw secret is shown exactly once."
        actions={
          <Button size="sm" onClick={() => setCreateOpen(true)} disabled={!canCreate}>
            <Plus data-icon="inline-start" /> Create key
          </Button>
        }
      />
      {!canCreate && (
        <Alert>
          <TriangleAlert />
          <AlertTitle>Read-only: no keys file configured</AlertTitle>
          <AlertDescription>
            Set <code className="text-xs">auth.keys_file</code> so keys can be created and revoked live; inline config keys can only change by editing the config.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard label="Active keys" icon={KeyRound} value={keys.data ? data.length : "—"} hint={`${orgs.length} org${orgs.length === 1 ? "" : "s"}`} loading={keys.isLoading} />
        <StatCard label="Admin-capable" icon={ShieldCheck} value={keys.data ? data.filter((k) => capabilitiesFor(k).manage_keys).length : "—"} hint="can create & delete keys" loading={keys.isLoading} />
        <StatCard label="Operator views" value={keys.data ? data.filter((k) => capabilitiesFor(k).view_ops).length : "—"} hint="viewer, operator, admin, finance" loading={keys.isLoading} />
        <StatCard label="Model-restricted" value={keys.data ? data.filter((k) => k.allowed_models.length > 0).length : "—"} hint="keys with an allow-list" loading={keys.isLoading} />
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={search} onChange={setSearch} placeholder="Search key, org, team, user…" />
        <Select value={orgF} onValueChange={setOrgF}>
          <SelectTrigger aria-label="Filter by org" size="sm" className="w-full sm:w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All orgs</SelectItem>
            {orgs.map((o) => (
              <SelectItem key={o} value={o}>
                {o}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={roleF} onValueChange={setRoleF}>
          <SelectTrigger aria-label="Filter by role" size="sm" className="w-full sm:w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All roles</SelectItem>
            <SelectItem value="tenant">Tenant</SelectItem>
            <SelectItem value="viewer">Viewer</SelectItem>
            <SelectItem value="operator">Operator</SelectItem>
            <SelectItem value="admin">Admin</SelectItem>
            <SelectItem value="finance">Finance</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {keys.isError && !keys.data ? (
        <ErrorState error={keys.error} onRetry={() => keys.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={filtered}
          loading={keys.isLoading}
          globalFilter={search}
          getRowId={(k) => k.key_id}
          initialSorting={[{ id: "identity", desc: false }]}
          empty={<EmptyState bordered={false} icon={KeyRound} title="No keys match" description="Adjust the filters, or create a key." />}
        />
      )}

      <Panel title="Roles and rights" description="Rights compose: a role and the cost_admin / ops_admin flags each grant access independently." contentClassName="px-0">
        <CapabilityMatrix />
      </Panel>

      <CreateKeyDialog open={createOpen} onOpenChange={setCreateOpen} orgs={orgs} onCreated={setCreated} />
      <RevealKeyDialog key={created?.key_id ?? "none"} result={created} onClose={() => setCreated(null)} />
      <ConfirmDialog
        open={!!victim}
        onOpenChange={(o) => !o && setVictim(null)}
        title={`Delete ${victim?.key_id}?`}
        description={
          <>
            <p>
              Requests using this key will fail with <b>401</b> immediately. Owner: <b>{victim?.org_id}</b>
              {victim?.team_id ? ` / ${victim.team_id}` : ""}.
            </p>
            <p>Historical usage rows keep the key ID for attribution.</p>
          </>
        }
        confirmLabel="Delete key"
        destructive
        typeToConfirm={victim?.key_id}
        pending={del.isPending}
        onConfirm={() => {
          if (victim) del.mutate(victim.key_id)
        }}
      />
    </Page>
  )
}
