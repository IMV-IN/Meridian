import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Check, Copy, KeyRound, Plus, SquareTerminal } from "lucide-react"
import { useState } from "react"
import { useNavigate, useSearchParams } from "react-router"
import { toast } from "sonner"

import { CodeBlock, useCopy } from "@/components/code"
import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, TimeAgo } from "@/components/misc"
import { Page, PageHeader, Panel } from "@/components/page"
import { EmptyState, ErrorState } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { ApiError } from "@/lib/api/client"
import { formatINR, formatSeconds, pluralize } from "@/lib/format"

import { atLeast, cloud, type ApiKeyView, type JobSpec, type JobSummary, type JobTemplate } from "../api"
import { JobStatusBadge } from "../components"
import { cliInstallLine, formatEnv, joinArgs, parseEnv, secondsToHours, splitArgs } from "../compute"
import { useApiKeys, useJobs, useJobTemplates, useOffers, useRentals } from "../hooks"
import { useCloud, useWorkspace } from "../session"

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.")
const IMAGE = /^[a-z0-9]([a-z0-9._:-]*[a-z0-9])?(\/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)+@sha256:[0-9a-f]{64}$/

function NewJobDialog({ open, onOpenChange, offerId }: { open: boolean; onOpenChange: (o: boolean) => void; offerId: string | null }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-2xl">
        <NewJobBody onClose={() => onOpenChange(false)} offerId={offerId} />
      </DialogContent>
    </Dialog>
  )
}

function NewJobBody({ onClose, offerId }: { onClose: () => void; offerId: string | null }) {
  const ws = useWorkspace()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const canRent = atLeast(ws.role, "admin")
  const offers = useOffers()
  const rentals = useRentals()
  const templates = useJobTemplates()
  const [template, setTemplate] = useState("")
  const [name, setName] = useState("")
  const [kind, setKind] = useState<"job" | "service">("job")
  const [image, setImage] = useState("")
  const [command, setCommand] = useState("")
  const [envText, setEnvText] = useState("")
  const [gpus, setGpus] = useState("1")
  const [replicas, setReplicas] = useState("1")
  const [hours, setHours] = useState("")
  const [where, setWhere] = useState(offerId ? `offer:${offerId}` : "auto")

  const active = (rentals.data ?? []).filter((r) => r.role === "consumer" && r.status === "active")
  const rentable = (offers.data ?? []).filter((o) => o.available && !o.mine)
  const parsed = parseEnv(envText)
  const nGpus = Number(gpus)
  const nReplicas = kind === "service" ? 1 : Number(replicas)
  const imageOk = IMAGE.test(image.trim())
  const numbersOk = Number.isInteger(nGpus) && nGpus >= 0 && nGpus <= 16 && Number.isInteger(nReplicas) && nReplicas >= 1 && nReplicas <= 64
  const timeout = hours.trim() === "" ? 0 : Math.round(Number(hours) * 3600)
  const hoursOk = hours.trim() === "" || (Number.isFinite(timeout) && timeout > 0 && timeout <= 30 * 86400)
  const offer = where.startsWith("offer:") ? rentable.find((o) => o.id === where.slice(6)) : undefined
  const tooBig = offer !== undefined && nGpus * nReplicas > offer.gpu_count

  const chosen = (templates.data ?? []).find((t) => t.id === template)
  // Selecting a template prefills every field below; each stays editable. "Custom" clears them.
  const pick = (id: string) => {
    const t = (templates.data ?? []).find((x) => x.id === id)
    setTemplate(t ? t.id : "")
    setKind(t?.kind ?? "job")
    setImage(t?.image ?? "")
    setCommand(t ? joinArgs(t.args) : "")
    setEnvText(t ? formatEnv(t.env) : "")
    setGpus(String(t?.gpus_per_replica ?? 1))
    setReplicas(String(t?.replicas ?? 1))
    setHours(t ? secondsToHours(t.timeout_seconds) : "")
  }

  const submit = useMutation({
    mutationFn: () => {
      const body: JobSpec = {
        kind,
        image: image.trim(),
        args: splitArgs(command),
        env: parsed.env,
        replicas: nReplicas,
        gpus_per_replica: nGpus,
        min_vram_gb: 0,
        timeout_seconds: timeout,
      }
      if (name.trim()) body.name = name.trim()
      if (chosen) body.template = chosen.id
      if (where.startsWith("offer:")) body.offer_id = where.slice(6)
      if (where.startsWith("rental:")) body.rental_id = where.slice(7)
      return cloud.submitJob(ws.id, body)
    },
    onSuccess: async (job) => {
      await Promise.all(["jobs", "rentals", "offers", "billing"].map((k) => qc.invalidateQueries({ queryKey: ["cloud", k, ws.id] })))
      toast.success(job.status === "queued" ? "Job queued" : "Job submitted")
      onClose()
      navigate(`/jobs/${encodeURIComponent(job.id)}`)
    },
  })
  const ready = imageOk && numbersOk && hoursOk && parsed.bad === null && !tooBig

  return (
    <>
      <DialogHeader>
        <DialogTitle>Run a job</DialogTitle>
        <DialogDescription>
          Runs your container on GPUs you own or rent. A job runs until it exits; a service keeps running and gets a port.
        </DialogDescription>
      </DialogHeader>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) submit.mutate()
        }}
      >
        <TemplatePicker templates={templates.data ?? []} value={template} onChange={pick} docs={chosen?.docs} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="job-name">Name (optional)</FieldLabel>
            <Input id="job-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. llama-finetune-v3" />
          </Field>
          <Field>
            <FieldLabel htmlFor="job-kind">Type</FieldLabel>
            <Select value={kind} onValueChange={(v) => setKind(v as "job" | "service")}>
              <SelectTrigger id="job-kind" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="job">Job: runs until it exits</SelectItem>
                <SelectItem value="service">Service: stays up, has a port</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <Field data-invalid={image !== "" && !imageOk}>
          <FieldLabel htmlFor="job-image">Container image</FieldLabel>
          <Input id="job-image" className="font-mono text-[12.5px]" spellCheck={false} autoComplete="off" value={image} onChange={(e) => setImage(e.target.value)} placeholder="docker.io/pytorch/pytorch@sha256:…" />
          {image !== "" && !imageOk ? (
            <FieldError>Use the full name with a digest: registry/repository@sha256: followed by 64 hex characters.</FieldError>
          ) : (
            <FieldDescription>
              Pinned by digest, so what runs is exactly what you tested. Get it with <code className="font-mono text-[0.85em]">docker inspect --format '{"{{index .RepoDigests 0}}"}' image:tag</code>.
            </FieldDescription>
          )}
        </Field>
        <Field>
          <FieldLabel htmlFor="job-cmd">Command</FieldLabel>
          <Textarea id="job-cmd" className="min-h-9 font-mono text-[12.5px]" spellCheck={false} autoComplete="off" rows={chosen ? 4 : 1} value={command} onChange={(e) => setCommand(e.target.value)} placeholder="python train.py --epochs 3" />
          <FieldDescription>
            {kind === "service" ? (
              <>
                Use <code className="font-mono text-[0.85em]">{"{{port}}"}</code> for the port to listen on and <code className="font-mono text-[0.85em]">{"{{service_token}}"}</code> for the API key to require.
              </>
            ) : (
              <>
                Leave empty to use the image's default. With several replicas, <code className="font-mono text-[0.85em]">{"{{rank}}"}</code> and <code className="font-mono text-[0.85em]">{"{{master}}"}</code> are filled in per replica.
              </>
            )}
          </FieldDescription>
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="job-gpus">GPUs per replica</FieldLabel>
            <Input id="job-gpus" inputMode="numeric" value={gpus} onChange={(e) => setGpus(e.target.value)} />
          </Field>
          <Field>
            <FieldLabel htmlFor="job-replicas">Replicas</FieldLabel>
            <Input id="job-replicas" inputMode="numeric" value={kind === "service" ? "1" : replicas} disabled={kind === "service"} onChange={(e) => setReplicas(e.target.value)} />
          </Field>
          <Field data-invalid={!hoursOk}>
            <FieldLabel htmlFor="job-hours">Stop after (hours)</FieldLabel>
            <Input id="job-hours" inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="No limit" />
          </Field>
        </div>
        {!numbersOk && <p role="alert" className="text-sm text-destructive">GPUs per replica must be 0–16 and replicas 1–64.</p>}
        {nReplicas > 1 && numbersOk && (
          <p className="text-sm text-muted-foreground">
            {pluralize(nReplicas, "replica")} start together or not at all. Each gets its rank and the address of every peer, so frameworks like PyTorch DDP can connect.
          </p>
        )}
        <Field>
          <FieldLabel htmlFor="job-where">Run on</FieldLabel>
          <Select value={where} onValueChange={setWhere}>
            <SelectTrigger id="job-where" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Your nodes and current rentals (no extra charge)</SelectItem>
              {active.map((r) => (
                <SelectItem key={r.id} value={`rental:${r.id}`}>
                  Rental: {r.gpu_count}× {r.offer.gpu_model}, {r.offer.region}
                </SelectItem>
              ))}
              {canRent &&
                rentable.map((o) => (
                  <SelectItem key={o.id} value={`offer:${o.id}`}>
                    Rent for this job: {o.gpu_count}× {o.gpu_model}, {o.region} · {o.hourly_total_paise === 0 ? "free" : `${formatINR(o.hourly_total_paise)}/hour`}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          {offer ? (
            tooBig ? (
              <FieldError>
                This job needs {nGpus * nReplicas} GPUs; the listing has {offer.gpu_count}.
              </FieldError>
            ) : (
              <FieldDescription>
                {offer.hourly_total_paise === 0
                  ? "Rents the whole listing, which is shared with you free of charge, and releases it when the job ends."
                  : `Rents the whole listing at ${formatINR(offer.hourly_total_paise)}/hour, billed per second, and releases it when the job ends.`}
              </FieldDescription>
            )
          ) : (
            where === "auto" && <FieldDescription>If no GPU is free the job waits in the queue.</FieldDescription>
          )}
        </Field>
        <Field data-invalid={parsed.bad !== null}>
          <FieldLabel htmlFor="job-env">Environment (optional)</FieldLabel>
          <Textarea id="job-env" className="font-mono text-[12.5px]" spellCheck={false} rows={3} value={envText} onChange={(e) => setEnvText(e.target.value)} placeholder={"WANDB_MODE=offline\nHF_HOME=/tmp/hf"} />
          {parsed.bad !== null ? (
            <FieldError>
              “{parsed.bad}” is not NAME=value. Names starting with MERIDIAN_ are reserved.
            </FieldError>
          ) : (
            <FieldDescription>One NAME=value per line. These are stored as plain text, so do not put secrets here.</FieldDescription>
          )}
        </Field>
        {submit.isError && (
          <p role="alert" className="text-sm text-destructive">
            {message(submit.error)}
          </p>
        )}
      </form>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={() => submit.mutate()} disabled={!ready || submit.isPending}>
          {submit.isPending && <Spinner data-icon="inline-start" />}
          {offer ? "Rent and run" : "Run"}
        </Button>
      </DialogFooter>
    </>
  )
}

function TemplatePicker({ templates, value, onChange, docs }: { templates: JobTemplate[]; value: string; onChange: (id: string) => void; docs?: string }) {
  if (templates.length === 0) return null
  const options = [...templates.map((t) => ({ id: t.id, title: t.title, summary: t.summary })), { id: "custom", title: "Custom", summary: "Your own image and command." }]
  return (
    <div className="space-y-2">
      <div id="job-template-label" className="text-sm font-medium">
        Start from a template
      </div>
      <RadioGroup aria-labelledby="job-template-label" className="grid gap-2 sm:grid-cols-2" value={value || "custom"} onValueChange={(v) => onChange(v === "custom" ? "" : v)}>
        {options.map((o) => (
          <label key={o.id} htmlFor={`tpl-${o.id}`} className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm has-data-checked:border-primary has-data-checked:bg-primary/5">
            <RadioGroupItem id={`tpl-${o.id}`} value={o.id} className="mt-0.5" />
            <span className="min-w-0">
              <span className="block font-medium">{o.title}</span>
              <span className="block text-xs text-muted-foreground">{o.summary}</span>
            </span>
          </label>
        ))}
      </RadioGroup>
      {docs && <p className="rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">{docs}</p>}
    </div>
  )
}

function CliPanel() {
  const ws = useWorkspace()
  const { info } = useCloud()
  const qc = useQueryClient()
  const keys = useApiKeys()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [role, setRole] = useState<ApiKeyView["role"]>("operator")
  const [token, setToken] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<ApiKeyView | null>(null)
  const { copied, copy } = useCopy(2500)
  const create = useMutation({
    mutationFn: () => cloud.createApiKey(ws.id, name.trim() || "cli", role),
    onSuccess: async (k) => {
      setToken(k.token)
      await qc.invalidateQueries({ queryKey: ["cloud", "api-keys", ws.id] })
    },
  })
  const revoke = useMutation({
    mutationFn: (k: ApiKeyView) => cloud.revokeApiKey(ws.id, k.id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["cloud", "api-keys", ws.id] })
      toast.success("API key revoked")
      setRevoking(null)
    },
  })
  const live = (keys.data ?? []).filter((k) => !k.revoked)
  const close = () => {
    setOpen(false)
    setToken(null)
    setName("")
    create.reset()
  }
  const snippet = `${cliInstallLine(info)}
export MERIDIAN_CLOUD_URL=${info.public_url}
export MERIDIAN_WORKSPACE=${ws.id}
export MERIDIAN_API_KEY=mck_…          # your API key

meridian compute offers
meridian compute run --offer <id> --follow \\
  --image docker.io/pytorch/pytorch@sha256:… -- python train.py`

  return (
    <Panel
      title="Run from your terminal"
      description="The meridian CLI rents GPUs and runs jobs with a workspace API key."
      actions={
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <KeyRound data-icon="inline-start" /> New API key
        </Button>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <CodeBlock language="shell" code={snippet} />
        <div className="min-w-0 text-sm">
          {live.length === 0 ? (
            <p className="text-muted-foreground">No API keys yet. Create one to use the CLI or call the API from CI.</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {live.map((k) => (
                <li key={k.id} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{k.name}</div>
                    <div className="text-xs text-muted-foreground">
                      <span className="font-mono">{k.prefix}…</span> · {k.role} · {k.last_used_at ? <>used <TimeAgo value={k.last_used_at} /></> : "never used"}
                    </div>
                  </div>
                  <Button variant="ghost" size="xs" onClick={() => setRevoking(k)}>
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : close())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{token ? "Copy your API key" : "New API key"}</DialogTitle>
            <DialogDescription>
              {token ? "This is the only time the key is shown. Store it somewhere safe." : "A key acts in this workspace only, with the role you choose."}
            </DialogDescription>
          </DialogHeader>
          {token ? (
            <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-2 pl-3">
              <code className="min-w-0 flex-1 font-mono text-[12.5px] break-all select-all">{token}</code>
              <Button size="sm" variant={copied ? "secondary" : "default"} onClick={() => void copy(token, "API key")}>
                {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
          ) : (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault()
                create.mutate()
              }}
            >
              <Field>
                <FieldLabel htmlFor="key-name">Name</FieldLabel>
                <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. training-ci" />
              </Field>
              <Field>
                <FieldLabel htmlFor="key-role">Role</FieldLabel>
                <Select value={role} onValueChange={(v) => setRole(v as ApiKeyView["role"])}>
                  <SelectTrigger id="key-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="viewer">Viewer: read jobs and billing</SelectItem>
                    <SelectItem value="operator">Operator: run and cancel jobs</SelectItem>
                    <SelectItem value="admin">Admin: also rent GPUs and spend credit</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {create.isError && (
                <p role="alert" className="text-sm text-destructive">
                  {message(create.error)}
                </p>
              )}
            </form>
          )}
          <DialogFooter>
            {token ? (
              <Button onClick={close}>Done</Button>
            ) : (
              <>
                <Button variant="outline" onClick={close}>
                  Cancel
                </Button>
                <Button onClick={() => create.mutate()} disabled={create.isPending}>
                  {create.isPending && <Spinner data-icon="inline-start" />}
                  Create key
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke ${revoking?.name ?? "this key"}?`}
        description="Anything using this key stops working immediately."
        confirmLabel="Revoke key"
        destructive
        pending={revoke.isPending}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking)
        }}
      />
    </Panel>
  )
}

const columns: ColumnDef<JobSummary, unknown>[] = [
  {
    accessorKey: "name",
    header: "Job",
    cell: ({ row: { original: j } }) => (
      <div className="flex flex-col">
        <span className="font-medium">{j.name}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{j.id}</span>
      </div>
    ),
  },
  { id: "status", header: "Status", accessorFn: (j) => j.status, cell: ({ row }) => <JobStatusBadge status={row.original.status} /> },
  { accessorKey: "kind", header: "Type", cell: ({ getValue }) => (getValue() === "service" ? "Service" : "Job") },
  {
    id: "size",
    header: "GPUs",
    meta: { align: "right" },
    accessorFn: (j) => j.replicas * j.gpus_per_replica,
    cell: ({ row: { original: j } }) => (j.replicas > 1 ? `${j.replicas} × ${j.gpus_per_replica}` : String(j.gpus_per_replica)),
  },
  { id: "time", header: "Run time", meta: { align: "right" }, accessorFn: (j) => j.seconds, cell: ({ row: { original: j } }) => (j.started_at ? formatSeconds(j.seconds) : "—") },
  { accessorKey: "created_by", header: "Submitted by", cell: ({ getValue }) => <span className="text-xs">{getValue() as string}</span> },
  { accessorKey: "created_at", header: "Submitted", cell: ({ getValue }) => <TimeAgo value={getValue() as string} className="text-xs" /> },
]

export default function JobsPage() {
  const ws = useWorkspace()
  const jobs = useJobs()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const canRun = atLeast(ws.role, "operator")
  const newOpen = params.get("new") === "1"
  const setNew = (o: boolean) => setParams(o ? { new: "1" } : {}, { replace: true })

  return (
    <Page>
      <PageHeader
        title="Jobs"
        description="Training runs, batch work and model services on your own nodes or on GPUs you rent. Each runs as a container pinned to an exact image."
        actions={
          canRun && (
            <Button size="sm" onClick={() => setNew(true)}>
              <Plus data-icon="inline-start" /> Run a job
            </Button>
          )
        }
      />
      {jobs.isError && !jobs.data ? (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={jobs.data ?? []}
          loading={jobs.isLoading}
          getRowId={(j) => j.id}
          onRowClick={(j) => navigate(`/jobs/${encodeURIComponent(j.id)}`)}
          empty={
            <EmptyState bordered={false} icon={SquareTerminal} title="No jobs yet" description="Run a container on a node you own, or rent GPUs from the marketplace for exactly as long as the job takes.">
              {canRun && (
                <Button size="sm" onClick={() => setNew(true)}>
                  Run a job
                </Button>
              )}
            </EmptyState>
          }
        />
      )}
      {atLeast(ws.role, "admin") && <CliPanel />}
      {canRun && <NewJobDialog open={newOpen} onOpenChange={setNew} offerId={params.get("offer")} />}
    </Page>
  )
}
