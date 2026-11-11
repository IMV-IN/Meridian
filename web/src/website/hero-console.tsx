// Figure 1: the cloud console of a sample workspace, recreated. Page titles and descriptions are copied
// from web/src/cloud/pages and web/src/pages/fleet. The demo job moves through the real statuses.
import { Cpu, CreditCard, LayoutGrid, Plus, ScrollText, SquareTerminal, Store, Ticket, Waypoints } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode } from "react"

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatINR, formatSeconds } from "@/lib/format"

import { advance, SAMPLE_JOBS, SAMPLE_OFFERS, stillJobs, USAGE, type JobStatus, type SampleJob } from "./sample"
import { CBadge, ConsoleFrame, type Tone } from "./ui"
import { useOnScreen, useReducedMotion } from "./site-lib"

const BADGE: Record<JobStatus, [Tone, string]> = {
  queued: ["wait", "Queued"],
  scheduled: ["wait", "Scheduled"],
  running: ["run", "Running"],
  succeeded: ["ok", "Succeeded"],
  failed: ["bad", "Failed"],
}
const runTime = (s: number) => (s === 0 ? "—" : formatSeconds(s))

const Head = ({ title, desc, action }: { title: string; desc: string; action?: ReactNode }) => (
  <div className="c-headrow">
    <div>
      <div className="c-h1">{title}</div>
      <p className="c-desc">{desc}</p>
    </div>
    {action}
  </div>
)
const Action = ({ children, out }: { children: ReactNode; out?: boolean }) => <span className={out ? "c-btn out" : "c-btn"}>{children}</span>

function Tiles({ items }: { items: [label: string, value: ReactNode, hint: string][] }) {
  return (
    <div className="c-stats">
      {items.map(([label, value, hint]) => (
        <div className="c-stat" key={label}>
          <small>{label}</small>
          <b className="num">{value}</b>
          <span>{hint}</span>
        </div>
      ))}
    </div>
  )
}

const Dim = ({ children }: { children: ReactNode }) => <span style={{ fontSize: 13, color: "var(--c-fg2)" }}>{children}</span>

function Jobs({ jobs }: { jobs: SampleJob[] }) {
  return (
    <>
      <Head
        title="Jobs"
        desc="Training runs, batch work and model services on your own nodes or on GPUs you rent. Each runs as a container pinned to an exact image."
        action={
          <Action>
            <Plus size={13} aria-hidden /> Run a job
          </Action>
        }
      />
      <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Jobs, sample data">
        <table className="c-table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th className="hide-sm">Type</th>
              <th className="r">GPUs</th>
              <th className="r">Run time</th>
              <th className="hide-sm">Submitted by</th>
              <th className="hide-sm">Submitted</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>
                  <b style={{ fontWeight: 500 }}>{j.name}</b>
                  <span className="id">{j.id}</span>
                </td>
                <td>
                  <CBadge tone={BADGE[j.status][0]}>{BADGE[j.status][1]}</CBadge>
                </td>
                <td className="hide-sm">{j.kind}</td>
                <td className="r num">{j.gpus}</td>
                <td className="r num">{runTime(j.t)}</td>
                <td className="hide-sm">{j.by}</td>
                <td className="hide-sm">{j.ago}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Tiles4({ n, used, listed = 0 }: { n: number; used: number; listed?: number }) {
  return (
    <div className="gpu-row">
      {Array.from({ length: n }, (_, i) => (
        <i key={i} className={`gpu ${i < used ? "busy" : i >= n - listed ? "listed" : ""}`} />
      ))}
    </div>
  )
}

export function Legend() {
  return (
    <div className="c-legend">
      <span>
        <i className="gpu busy" />
        In use
      </span>
      <span>
        <i className="gpu" />
        Free
      </span>
      <span>
        <i className="gpu listed" />
        Listed on the marketplace
      </span>
    </div>
  )
}

function Nodes({ jobs }: { jobs: SampleJob[] }) {
  const busy = jobs.filter((j) => j.status === "running" || j.status === "scheduled").reduce((a, j) => a + j.gpus, 0)
  const u1 = Math.min(4, busy)
  const u2 = Math.max(0, Math.min(4, busy - 4))
  const rows: [string, string, ReactNode, string, string][] = [
    ["gpu-01", "A100-SXM4-80GB × 4", <Tiles4 key="1" n={4} used={u1} />, `${(4 - u1) * 80} GB`, "2 seconds ago"],
    ["gpu-02", "A100-SXM4-80GB × 4", <Tiles4 key="2" n={4} used={u2} />, `${(4 - u2) * 80} GB`, "4 seconds ago"],
    ["lab-ws-07", "RTX A6000 × 2", <Tiles4 key="3" n={2} used={0} listed={2} />, "96 GB", "3 seconds ago"],
  ]
  return (
    <>
      <Head
        title="GPU nodes"
        desc="Hosts running the meridian-node agent. A node serves traffic only while its lease is valid and an engine reports Ready."
        action={
          <Action>
            <Plus size={13} aria-hidden /> Enroll node
          </Action>
        }
      />
      <Tiles
        items={[
          ["Nodes online", <>3<Dim>/3</Dim></>, "0 lease expired, 0 revoked"],
          ["GPUs in use", <>{busy}<Dim>/10</Dim></>, "2 listed on the marketplace"],
          ["Allocatable VRAM", `${(10 - busy - 2) * 80 + 96} GB`, "free for placement"],
          ["Pending approvals", "0", "Review enrollment queue"],
        ]}
      />
      <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Nodes, sample data">
        <table className="c-table">
          <thead>
            <tr>
              <th>Node</th>
              <th>Status</th>
              <th>GPUs</th>
              <th>Lease</th>
              <th className="r">Free VRAM</th>
              <th>Last observed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, sub, tiles, free, seen]) => (
              <tr key={name}>
                <td>
                  {name}
                  <span className="id">{sub}</span>
                </td>
                <td>
                  <CBadge tone="ok">Online</CBadge>
                </td>
                <td>{tiles}</td>
                <td>Valid</td>
                <td className="r num">{free}</td>
                <td>{seen}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Legend />
    </>
  )
}

function Billing() {
  return (
    <>
      <Head title="Billing" desc="Prepaid credit in rupees. Rentals draw it down per second; GPUs you rent out add to it." />
      <Tiles
        items={[
          ["Available credit", formatINR(4821000), "Balance, ready to spend"],
          ["Spending now", <>{formatINR(0)} <Dim>/hour</Dim></>, "No rentals running"],
          ["Spent on rentals", formatINR(195840), `Includes ${formatINR(3840)} in fees`],
          ["Earned from rentals", formatINR(720000), "You keep the full price you set"],
        ]}
      />
      <div className="c-headrow" style={{ marginTop: 18 }}>
        <div>
          <div style={{ fontWeight: 600 }}>GPU-hour usage</div>
          <p className="c-desc" style={{ fontSize: 12 }}>
            Per person, in UTC months. Own-node jobs are free but count against quotas; rented GPUs are paid for with credit. Quotas and groups are set in Settings → Members.
          </p>
        </div>
        <span className="c-select">2026-11</span>
        <Action out>CSV</Action>
      </div>
      <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Usage, sample data" style={{ marginTop: 10 }}>
        <table className="c-table">
          <thead>
            <tr>
              <th>Person</th>
              <th>Group</th>
              <th className="r">Own GPU-hours</th>
              <th className="r">Quota</th>
              <th className="r">Left</th>
            </tr>
          </thead>
          <tbody>
            {USAGE["2026-11"].people.map(([email, group, hours, quota]) => (
              <tr key={email}>
                <td>{email}</td>
                <td>{group}</td>
                <td className="r num">{hours.toFixed(2)}</td>
                <td className="r num">{quota}</td>
                <td className="r num">{Math.max(0, quota - hours).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Market() {
  return (
    <>
      <Head
        title="Marketplace"
        desc="Rent GPUs from data centres across India, or list your own. Renters pay the provider's price plus a 2% Meridian fee, billed per second."
        action={
          <Action out>
            <Plus size={13} aria-hidden /> List a node
          </Action>
        }
      />
      <div className="c-tabs">
        <span className="on">Rent GPUs</span>
        <span>Rentals</span>
        <span>Your listings</span>
      </div>
      <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Rentals, sample data" style={{ marginTop: 12 }}>
        <table className="c-table">
          <thead>
            <tr>
              <th>Listing</th>
              <th>GPUs</th>
              <th>Region</th>
              <th className="r">Per GPU-hour</th>
              <th className="r">Whole, per hour (+fee)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {SAMPLE_OFFERS.map((o) => (
              <tr key={o.title}>
                <td>{o.title}</td>
                <td>
                  {o.gpu_count} × {o.gpu_model}
                </td>
                <td>{o.region}</td>
                <td className="r num">{formatINR(o.price_paise_per_gpu_hour)}</td>
                <td className="r num">{formatINR(o.hourly_total_paise)}</td>
                <td>{o.available ? <CBadge tone="ok">Available</CBadge> : <CBadge tone="neutral">Rented</CBadge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

type View = "market" | "jobs" | "billing" | "nodes"
const VIEWS: Record<View, { crumb: string; path: string; icon: ReactNode }> = {
  market: { crumb: "Marketplace", path: "marketplace", icon: <Store /> },
  jobs: { crumb: "Jobs", path: "jobs", icon: <SquareTerminal /> },
  billing: { crumb: "Billing", path: "billing", icon: <CreditCard /> },
  nodes: { crumb: "Nodes", path: "fleet / nodes", icon: <Cpu /> },
}

const Static = ({ icon, children }: { icon: ReactNode; children: ReactNode }) => (
  <span className="c-item c-hide" aria-hidden="true">
    {icon}
    {children}
  </span>
)
const Group = ({ children }: { children: ReactNode }) => <div className="c-group">{children}</div>

const STILL = stillJobs()

export function HeroConsole() {
  const reduced = useReducedMotion()
  const [view, setView] = useState<View>("jobs")
  const [live, setJobs] = useState<SampleJob[]>(SAMPLE_JOBS)
  const jobs = reduced ? STILL : live // under reduced motion the demo job sits at Running
  const visible = useRef(false)
  const frame = useOnScreen<HTMLDivElement>((v) => (visible.current = v))
  const tick = useRef(0)

  useEffect(() => {
    if (reduced) return
    // Only while the console is on screen and the tab is visible.
    const id = window.setInterval(() => {
      if (visible.current && !document.hidden) setJobs((js) => advance(js, ++tick.current))
    }, 2600)
    return () => window.clearInterval(id)
  }, [reduced])

  const trigger = (v: View, label: string) => (
    <TabsTrigger value={v} className="c-item" id={`ctab-${v}`}>
      {VIEWS[v].icon}
      {label}
    </TabsTrigger>
  )
  return (
    <ConsoleFrame ref={frame} id="hero-console">
      <div className="chrome" aria-hidden="true">
        <i />
        <i />
        <i />
        <span className="url">your-meridian-cloud / ui / {VIEWS[view].path}</span>
      </div>
      <Tabs value={view} onValueChange={(v) => setView(v as View)} orientation="vertical" className="shell">
        <TabsList className="c-side" aria-label="Console pages (sample workspace)">
          <div className="c-ws">
            <span className="logo">P</span>
            <div>
              <b>Physics Dept. (sample)</b>
              <span>Meridian Cloud, admin</span>
            </div>
          </div>
          <Group>Workspace</Group>
          <Static icon={<LayoutGrid />}>Overview</Static>
          <Static icon={<Waypoints />}>Gateways</Static>
          <Group>Compute</Group>
          {trigger("market", "Marketplace")}
          {trigger("jobs", "Jobs")}
          {trigger("billing", "Billing")}
          <Group>GPU fleet</Group>
          {trigger("nodes", "Nodes")}
          <Static icon={<Ticket />}>Enrollment</Static>
          <Static icon={<ScrollText />}>Audit log</Static>
        </TabsList>
        <div className="c-main">
          <div className="c-top" aria-hidden="true">
            <span>Meridian Cloud</span>
            <span>/</span>
            <b>{VIEWS[view].crumb}</b>
            <span className="search">
              Search or jump to… <span>⌘K</span>
            </span>
          </div>
          <TabsContent value={view} className="c-body" aria-live="off">
            {view === "jobs" && <Jobs jobs={jobs} />}
            {view === "nodes" && <Nodes jobs={jobs} />}
            {view === "billing" && <Billing />}
            {view === "market" && <Market />}
          </TabsContent>
        </div>
      </Tabs>
    </ConsoleFrame>
  )
}
