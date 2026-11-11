import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useParams } from "react-router"
import { toast } from "sonner"

import { CodeBlock, CopyText } from "@/components/code"
import { ConfirmDialog, Timestamp } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel } from "@/components/page"
import { ErrorState, LoadingRows } from "@/components/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api/client"
import { formatSeconds } from "@/lib/format"

import { atLeast, cloud, type JobDetail } from "../api"
import { JobStatusBadge, PhaseBadge } from "../components"
import { useJob } from "../hooks"
import { useWorkspace } from "../session"

const DONE = ["succeeded", "failed", "cancelled"]

function ServicePanel({ job }: { job: JobDetail }) {
  const s = job.service
  if (!s) return null
  const gateway = `# Add to a Meridian gateway's config.yaml to route to this service
backends:
  - name: ${job.name}
    url: ${s.endpoint || "<endpoint appears once the service is running>"}
    auth_header: "Bearer \${MERIDIAN_SERVICE_TOKEN}"   # export the token below; never write it in the file`
  return (
    <Panel title="Service endpoint" description="Reachable where the host's network allows. The token is what the service was told to require.">
      <div className="space-y-4">
        <KeyValueGrid
          items={[
            { label: "Endpoint", value: s.endpoint ? <CopyText value={s.endpoint} /> : "Waiting for the service to start", mono: true },
            { label: "Bearer token", value: s.token ? <CopyText value={s.token} /> : "Visible to operators and admins", mono: true },
          ]}
        />
        <CodeBlock language="yaml" code={gateway} />
      </div>
    </Panel>
  )
}

export default function JobDetailPage() {
  const { jobId = "" } = useParams()
  const ws = useWorkspace()
  const qc = useQueryClient()
  const job = useJob(jobId)
  const [confirm, setConfirm] = useState(false)
  const cancel = useMutation({
    mutationFn: () => cloud.cancelJob(ws.id, jobId),
    onSuccess: async () => {
      await Promise.all(["job", "jobs", "rentals", "billing"].map((k) => qc.invalidateQueries({ queryKey: ["cloud", k, ws.id] })))
      toast.success("Job cancelled")
      setConfirm(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not cancel the job"),
  })

  if (job.isError && !job.data) return <Page><ErrorState error={job.error} onRetry={() => job.refetch()} /></Page>
  if (!job.data) return <Page><LoadingRows rows={8} /></Page>
  const j = job.data
  const done = DONE.includes(j.status)
  const command = j.args.length ? j.args.join(" ") : "(image default)"

  return (
    <Page>
      <PageHeader
        title={j.name}
        eyebrow={<span className="font-mono text-xs">{j.id}</span>}
        description={<span className="font-mono text-xs break-all">{j.image}</span>}
        actions={
          <>
            <JobStatusBadge status={j.status} />
            {!done && atLeast(ws.role, "operator") && (
              <Button size="sm" variant="outline" onClick={() => setConfirm(true)}>
                {j.kind === "service" ? "Stop service" : "Cancel job"}
              </Button>
            )}
          </>
        }
      />
      {j.message && (
        <Alert variant={j.status === "failed" ? "destructive" : "default"}>
          <AlertTitle>{j.status === "queued" ? "Waiting" : j.status === "failed" ? "Why it failed" : "Note"}</AlertTitle>
          <AlertDescription>{j.message}</AlertDescription>
        </Alert>
      )}
      <Panel title="Run">
        <KeyValueGrid
          columns={4}
          items={[
            { label: "Type", value: j.kind === "service" ? "Service" : "Job" },
            { label: "Size", value: `${j.replicas} × ${j.gpus_per_replica} GPU` },
            { label: "Run time", value: j.started_at ? formatSeconds(j.seconds) : "Not started" },
            { label: "Time limit", value: j.timeout_seconds ? formatSeconds(j.timeout_seconds) : "None" },
            { label: "Submitted", value: <Timestamp value={j.created_at} /> },
            { label: "Submitted by", value: j.created_by },
            { label: "Runs on", value: j.rental_id ? "Rented GPUs" : "Your nodes and rentals" },
            { label: "Command", value: command, mono: true },
          ]}
        />
      </Panel>
      {j.kind === "service" && <ServicePanel job={j} />}
      {j.placements.length === 0 ? (
        <Panel title="Replicas">
          <p className="text-sm text-muted-foreground">
            {done ? "This job never started, so it has no replicas or logs." : "Not placed yet. Replicas appear here once GPUs are free."}
          </p>
        </Panel>
      ) : (
        j.placements.map((p) => (
          <Panel
            key={p.rank}
            title={j.replicas > 1 ? `Replica ${p.rank}` : "Replica"}
            description={`${p.node_name || p.node_id} · ${p.gpus} GPU${p.gpus === 1 ? "" : "s"}${p.exit_code !== null ? ` · exit code ${p.exit_code}` : ""}`}
            actions={<PhaseBadge phase={p.phase} />}
          >
            {p.message && <p className="mb-3 text-sm text-muted-foreground">{p.message}</p>}
            {p.log_tail ? (
              <CodeBlock code={p.log_tail.replace(/\n$/, "")} maxHeight={420} wrap />
            ) : (
              <p className="text-sm text-muted-foreground">{p.phase === "Pending" ? "Waiting for the node to start it." : "No output yet."}</p>
            )}
            {p.log_tail && <p className="mt-2 text-xs text-muted-foreground">The last 8 KB of output. Write full logs to your own storage from the job.</p>}
          </Panel>
        ))
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={j.kind === "service" ? "Stop this service?" : "Cancel this job?"}
        description="Its containers are stopped and the GPUs are freed. This cannot be undone."
        confirmLabel={j.kind === "service" ? "Stop service" : "Cancel job"}
        destructive
        pending={cancel.isPending}
        onConfirm={() => cancel.mutate()}
      />
    </Page>
  )
}
