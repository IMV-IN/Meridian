import { Cpu, PlugZap } from "lucide-react"
import type { ReactNode } from "react"
import { Link } from "react-router"

import { Page, PageHeader } from "@/components/page"
import { NODE_STATUS_META, StatusBadge } from "@/components/status-badge"
import { EmptyState, ErrorState } from "@/components/states"
import { Button } from "@/components/ui/button"
import { useConfig, useControl, useControlOverview } from "@/hooks/queries"
import type { NodeStatus } from "@/lib/api/types"
import { formatRelative } from "@/lib/format"
import { useMode } from "@/providers/mode-provider"

/** Renders children only when a control-plane URL is configured and reachable. */
export function ControlGate({ title, description, actions, children }: { title: string; description: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const { api, baseUrl } = useControl()
  const overview = useControlOverview()
  const config = useConfig()
  const cloud = useMode().mode === "cloud"
  if (!api) {
    const suggested = config.data?.control_plane.enabled ? config.data.control_plane.url : null
    return (
      <Page>
        <PageHeader title={title} description={description} />
        <EmptyState
          icon={Cpu}
          title="Connect meridian-control"
          description={
            <>
              Fleet pages talk to the control plane directly. Add its URL in settings
              {suggested ? (
                <>
                  {" "}
                  — this gateway syncs managed engines from <code className="text-xs">{suggested}</code>
                </>
              ) : null}
              .
            </>
          }
        >
          <Button size="sm" asChild>
            <Link to="/settings?tab=control">
              <PlugZap data-icon="inline-start" /> Connect control plane
            </Link>
          </Button>
        </EmptyState>
      </Page>
    )
  }
  if (overview.isError && !overview.data) {
    return (
      <Page>
        <PageHeader title={title} description={description} />
        <ErrorState error={overview.error} title={cloud ? "Couldn't load the fleet" : `Can't reach ${baseUrl}`} onRetry={() => overview.refetch()} />
        {!cloud && (
        <p className="text-center text-sm text-muted-foreground">
          Check the URL and CORS in{" "}
          <Link className="underline underline-offset-2" to="/settings?tab=control">
            Settings → Control plane
          </Link>
          .
        </p>
        )}
      </Page>
    )
  }
  return (
    <Page>
      <PageHeader title={title} description={description} actions={actions} />
      {children}
    </Page>
  )
}

export function NodeStatusBadge({ status, size = "md" }: { status: NodeStatus; size?: "sm" | "md" }) {
  const meta = NODE_STATUS_META[status] ?? { tone: "neutral" as const, label: status }
  return (
    <StatusBadge tone={meta.tone} size={size}>
      {meta.label}
    </StatusBadge>
  )
}

export function LeaseText({ expires, status }: { expires: string | null; status: NodeStatus }) {
  if (!expires) return <span className="text-muted-foreground">no session</span>
  const rel = formatRelative(expires)
  return <span className={status === "online" ? "" : "text-muted-foreground"}>{status === "online" ? `expires ${rel}` : `expired ${rel}`}</span>
}

export const AUDIT_KIND: Record<string, { label: string; tone: "good" | "info" | "warning" | "critical" | "neutral" }> = {
  enrolled_auto: { label: "Enrolled (auto-approved)", tone: "good" },
  enrolled_approved: { label: "Enrolled (approved)", tone: "good" },
  claim_created: { label: "Claim created", tone: "warning" },
  session_established: { label: "Session established", tone: "info" },
  cert_rotated: { label: "Certificate rotated", tone: "info" },
  node_revoked: { label: "Node revoked", tone: "critical" },
  control_restore: { label: "Control plane restore", tone: "warning" },
}
