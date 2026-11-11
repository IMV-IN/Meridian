// Small recreations of console screens, with sample values. Each is a picture (role="img"), not a control.
import { CBadge, ConsoleFrame } from "./ui"
import { Legend } from "./hero-console"

/** Enrollment step: the Nodes table, in brief. */
export function NodesMini() {
  const tiles = (n: number, busy: number, listed = 0) => (
    <div className="gpu-row">
      {Array.from({ length: n }, (_, i) => (
        <i key={i} className={`gpu ${i < busy ? "busy" : i >= n - listed ? "listed" : ""}`} />
      ))}
    </div>
  )
  const rows: [string, string, number, number, number, string][] = [
    ["gpu-01", "A100 80 GB × 4", 4, 2, 0, "160 GB"],
    ["gpu-02", "A100 80 GB × 4", 4, 4, 0, "0 B"],
    ["lab-ws-07", "RTX A6000 × 2", 2, 0, 2, "96 GB"],
  ]
  return (
    <ConsoleFrame label="Nodes page, sample data: three nodes online, with GPUs in use, free or listed on the marketplace">
      <div className="c-body">
        <table className="c-table" style={{ minWidth: 0 }}>
          <thead>
            <tr>
              <th>Node</th>
              <th>Status</th>
              <th>GPUs</th>
              <th className="r">Free VRAM</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, sub, n, busy, listed, free]) => (
              <tr key={name}>
                <td>
                  {name} <span className="id">{sub}</span>
                </td>
                <td>
                  <CBadge tone="ok">Online</CBadge>
                </td>
                <td>{tiles(n, busy, listed)}</td>
                <td className="r num">{free}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Legend />
      </div>
    </ConsoleFrame>
  )
}

/** A finished two-node job, as its page shows it. */
export function JobPageSample() {
  return (
    <ConsoleFrame label="Job page for a sample two-node training job: succeeded, two replicas of four GPUs, with the end of its output">
      <div className="c-body">
        <div className="c-headrow">
          <div>
            <span className="id" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--c-fg2)" }}>
              job_3b90e1c4a7f25d6e8c01
            </span>
            <div className="c-h1" style={{ fontSize: 15 }}>
              resnet-ddp
            </div>
          </div>
          <CBadge tone="ok">Succeeded</CBadge>
        </div>
        <div className="c-stats" style={{ gridTemplateColumns: "repeat(3,1fr)", marginTop: 12 }}>
          <div className="c-stat">
            <small>Size</small>
            <b style={{ fontSize: 15 }}>2 × 4 GPU</b>
          </div>
          <div className="c-stat">
            <small>Run time</small>
            <b style={{ fontSize: 15 }}>1h 21m</b>
          </div>
          <div className="c-stat">
            <small>Runs on</small>
            <b style={{ fontSize: 15 }}>Your nodes and rentals</b>
          </div>
        </div>
        <div style={{ marginTop: 12, fontWeight: 600 }}>Replica 0</div>
        <p className="c-note" style={{ marginTop: 2 }}>
          gpu-01 · 4 GPUs · exit code 0
        </p>
        <pre className="c-log">{"epoch 30/30  loss 0.912  top1 0.761\ncheckpoint uploaded: ckpt-30.pt"}</pre>
        <p className="c-note">The last 8 KB of output. Write full logs to your own storage from the job.</p>
        <div style={{ marginTop: 12, fontWeight: 600 }}>Replica 1</div>
        <p className="c-note" style={{ marginTop: 2 }}>
          gpu-02 · 4 GPUs · exit code 0
        </p>
      </div>
    </ConsoleFrame>
  )
}

/** Settings, Members, Limits: the real dialog. */
export function ComputeLimitsDialog() {
  return (
    <ConsoleFrame label="Compute limits dialog for a sample student: group cs-601-deep-learning, monthly GPU-hour quota 10">
      <div className="c-body">
        <div className="c-h1" style={{ fontSize: 15 }}>
          Compute limits for student01@example.ac.in
        </div>
        <p className="c-desc" style={{ fontSize: 12 }}>
          The group appears in usage reports. The quota caps GPU-hours per UTC month on your own nodes; rented GPUs are limited by credit.
        </p>
        <div className="form-mock" style={{ marginTop: 14, gridTemplateColumns: "1fr 1fr" }}>
          <div>
            <label>Group</label>
            <div className="f">cs-601-deep-learning</div>
          </div>
          <div>
            <label>Monthly GPU-hour quota</label>
            <div className="f">10</div>
          </div>
        </div>
        <p className="help" style={{ marginTop: 8 }}>
          Leave empty to use the workspace default. Running jobs are never stopped; new ones wait or are refused once the quota is used.
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
          <span className="c-btn out">Cancel</span>
          <span className="c-btn">Save limits</span>
        </div>
      </div>
    </ConsoleFrame>
  )
}

/** Settings, Members: the workspace default quota panel. */
export function DefaultQuotaSample() {
  return (
    <ConsoleFrame label="GPU-hour quota panel, sample: a workspace default of 10 GPU-hours per month">
      <div className="c-body">
        <div className="c-h1" style={{ fontSize: 15 }}>
          GPU-hour quota
        </div>
        <p className="c-desc" style={{ fontSize: 12 }}>
          Monthly GPU-hours each member may use on this workspace's own nodes, unless they have a quota of their own. Resets on the first of each UTC month.
        </p>
        <div className="form-mock" style={{ marginTop: 14 }}>
          <div>
            <label>Default quota (GPU-hours per month)</label>
            <div style={{ display: "flex", gap: 8 }}>
              <div className="f" style={{ width: 160 }}>
                10
              </div>
              <span className="c-btn">Save</span>
            </div>
          </div>
        </div>
      </div>
    </ConsoleFrame>
  )
}

/** A running JupyterLab job: the Service endpoint panel with the notebook link. */
export function ServicePanelSample() {
  return (
    <ConsoleFrame label="Service endpoint panel for a sample JupyterLab job: the notebook link with its token">
      <div className="c-body">
        <div className="c-headrow">
          <div>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--c-fg2)" }}>job_5d02c91e7ab34f6d1e90</span>
            <div className="c-h1" style={{ fontSize: 15 }}>
              notebook-anita
            </div>
          </div>
          <CBadge tone="run">Running</CBadge>
        </div>
        <div style={{ marginTop: 14, fontWeight: 600 }}>Service endpoint</div>
        <p className="c-desc" style={{ fontSize: 12 }}>
          Reachable where the host's network allows. The token is what the service was told to require.
        </p>
        <div style={{ marginTop: 10, fontWeight: 600, fontSize: 12.5 }}>Open JupyterLab in your browser</div>
        <pre className="c-log">{"https://<endpoint>/lab?token=<token>"}</pre>
      </div>
    </ConsoleFrame>
  )
}
