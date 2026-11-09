import { ArrowRight, Check, Cpu, Network, Ticket, Timer, UserPlus, Zap } from "lucide-react"
import { Link, useNavigate } from "react-router"

import { Page, PageHeader, Panel } from "@/components/page"
import { StatCard } from "@/components/stat"
import { StatusBadge } from "@/components/status-badge"
import { TimeAgo } from "@/components/misc"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useControlOverview } from "@/hooks/queries"
import { formatBytes, formatMs, formatNumber, titleCase } from "@/lib/format"
import { cn } from "@/lib/utils"

import { atLeast } from "../api"
import { GatewayStatusBadge } from "../components"
import { useGateways, useMembers } from "../hooks"
import { useCloud, useWorkspace } from "../session"

function Step({ done, title, body, to, cta }: { done: boolean; title: string; body: string; to: string; cta: string }) {
  return (
    <li className="flex items-start gap-3 py-3">
      <span
        className={cn(
          "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border text-xs",
          done && "border-transparent bg-(--status-good) text-white",
        )}
      >
        {done && <Check className="size-3.5" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn("block text-sm font-medium", done && "text-muted-foreground line-through")}>{title}</span>
        <span className="block text-xs text-muted-foreground">{body}</span>
      </span>
      {!done && (
        <Button size="sm" variant="outline" asChild>
          <Link to={to}>{cta}</Link>
        </Button>
      )}
    </li>
  )
}

export default function CloudOverview() {
  const ws = useWorkspace()
  const { me } = useCloud()
  const navigate = useNavigate()
  const gateways = useGateways()
  const fleet = useControlOverview()
  const members = useMembers()
  const gws = gateways.data ?? []
  const online = gws.filter((g) => g.online)
  const rps = online.reduce((a, g) => a + (g.window.rps ?? 0), 0)
  const p95s = online.map((g) => g.window.p95_ms).filter((v): v is number => typeof v === "number")
  const backendsTotal = online.reduce((a, g) => a + g.backends_total, 0)
  const backendsHealthy = online.reduce((a, g) => a + g.backends_healthy, 0)
  const f = fleet.data
  const memberCount = members.data?.members.length ?? 1
  const steps = [
    { done: gws.length > 0, title: "Connect a gateway", body: "Run Meridian in your network with a workspace token.", to: "/gateways?connect=1", cta: "Connect" },
    { done: (f?.nodes.total ?? 0) > 0, title: "Enroll a GPU node", body: "Install meridian-node on a GPU host with a join token.", to: "/fleet/enrollment?mint=1", cta: "Mint token" },
    { done: memberCount > 1 || (members.data?.invitations.length ?? 0) > 0, title: "Invite your team", body: "Give operators and viewers their own sign-in.", to: "/settings?tab=members", cta: "Invite" },
  ]
  const setupDone = steps.every((s) => s.done)

  return (
    <Page>
      <PageHeader
        eyebrow={ws.name}
        title={`Good to see you${me?.user.name ? `, ${me.user.name.split(" ")[0]}` : ""}`}
        description="Health of every gateway and GPU node in this workspace. Gateways report metadata only — prompts and completions never leave your network."
        actions={
          atLeast(ws.role, "admin") && (
            <Button size="sm" onClick={() => navigate("/gateways?connect=1")}>
              <Network data-icon="inline-start" /> Connect gateway
            </Button>
          )
        }
      />

      {!setupDone && (
        <Panel title="Get started" description="Three steps to a managed inference fleet">
          <ol className="divide-y">
            {steps.map((s) => (
              <Step key={s.title} {...s} />
            ))}
          </ol>
        </Panel>
      )}

      <div className="grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4">
        <StatCard
          label="Gateways online"
          icon={Network}
          value={
            <>
              {online.length}
              <span className="text-base font-normal text-muted-foreground">/{gws.length}</span>
            </>
          }
          tone={!gws.length ? "neutral" : online.length === gws.length ? "good" : "warning"}
          hint={`${backendsHealthy}/${backendsTotal} backends healthy`}
          loading={gateways.isLoading}
        />
        <StatCard label="Throughput" icon={Zap} value={formatNumber(rps, { digits: rps < 10 ? 1 : 0 })} unit="req/s" hint="last report interval, all gateways" loading={gateways.isLoading} />
        <StatCard label="Worst p95 latency" icon={Timer} value={p95s.length ? formatMs(Math.max(...p95s)) : "—"} hint="across online gateways" loading={gateways.isLoading} />
        <StatCard
          label="GPU nodes online"
          icon={Cpu}
          value={
            <>
              {f?.nodes.by_status.online ?? 0}
              <span className="text-base font-normal text-muted-foreground">/{f?.nodes.total ?? 0}</span>
            </>
          }
          hint={f ? `${f.engines.ready}/${f.engines.total} engines ready · ${formatBytes(f.allocatable_vram_bytes, 0)} free` : undefined}
          tone={f && f.engines.failed > 0 ? "critical" : "neutral"}
          loading={fleet.isLoading}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Gateways"
          description="Latest report from each gateway process"
          actions={
            <Button variant="ghost" size="xs" asChild>
              <Link to="/gateways">
                All gateways
              </Link>
            </Button>
          }
          contentClassName="px-0"
        >
          {gws.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No gateway has reported yet. Connect one to see live health here.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4 text-xs">Gateway</TableHead>
                  <TableHead className="text-xs">Status</TableHead>
                  <TableHead className="text-right text-xs">Backends</TableHead>
                  <TableHead className="text-right text-xs">req/s</TableHead>
                  <TableHead className="text-right text-xs">p95</TableHead>
                  <TableHead className="pr-4 text-xs">Last report</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className="tabular">
                {gws.map((g) => (
                  <TableRow key={g.gateway_id} className="cursor-pointer" onClick={() => navigate(`/gateways/${encodeURIComponent(g.gateway_id)}`)}>
                    <TableCell className="pl-4">
                      <div className="font-medium">{g.name}</div>
                      <div className="text-xs text-muted-foreground">
                        v{g.version} · {g.strategy ? titleCase(g.strategy) : "—"}
                      </div>
                    </TableCell>
                    <TableCell>
                      <GatewayStatusBadge g={g} />
                    </TableCell>
                    <TableCell className="text-right">
                      {g.backends_healthy}/{g.backends_total}
                    </TableCell>
                    <TableCell className="text-right">{formatNumber(g.window.rps ?? null, { digits: 1 })}</TableCell>
                    <TableCell className="text-right">{formatMs(g.window.p95_ms ?? null)}</TableCell>
                    <TableCell className="pr-4 text-xs">
                      <TimeAgo value={g.last_seen_at} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Panel>
        <Panel
          title="GPU fleet"
          actions={
            <Button variant="ghost" size="xs" asChild>
              <Link to="/fleet">
                All nodes
              </Link>
            </Button>
          }
        >
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-muted-foreground">Routable engines</div>
              <div className="text-xl font-semibold">{f?.routable_endpoints ?? "—"}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Pending approvals</div>
              <div className="text-xl font-semibold">{f?.claims_pending ?? "—"}</div>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {!!f?.claims_pending && (
              <Link to="/fleet/enrollment" className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted/50">
                <StatusBadge tone="warning" size="sm" icon={Ticket}>
                  {f.claims_pending} node{f.claims_pending === 1 ? "" : "s"} awaiting approval
                </StatusBadge>
                <ArrowRight className="size-3.5 text-muted-foreground" />
              </Link>
            )}
            {!!f?.engines.failed && (
              <Link to="/fleet" className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted/50">
                <StatusBadge tone="critical" size="sm">
                  {f.engines.failed} engine{f.engines.failed === 1 ? "" : "s"} failed
                </StatusBadge>
                <ArrowRight className="size-3.5 text-muted-foreground" />
              </Link>
            )}
            {atLeast(ws.role, "admin") && (
              <Link to="/settings?tab=members" className="flex items-center justify-between rounded-md border px-3 py-2 text-sm hover:bg-muted/50">
                <span className="flex items-center gap-2 text-sm">
                  <UserPlus className="size-4 text-muted-foreground" /> {memberCount} member{memberCount === 1 ? "" : "s"}
                </span>
                <ArrowRight className="size-3.5 text-muted-foreground" />
              </Link>
            )}
          </div>
        </Panel>
      </div>
    </Page>
  )
}
