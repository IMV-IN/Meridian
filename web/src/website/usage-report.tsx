// Figure 3: Billing, GPU-hour usage (web/src/cloud/pages/billing.tsx) as an admin sees it, with sample people.
// Other members see only their own row and the workspace totals. Never calls the API.
import { useState } from "react"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

import { USAGE } from "./sample"
import { ConsoleFrame } from "./ui"

const hours = (n: number) => n.toFixed(2)

export function UsageReport() {
  const [month, setMonth] = useState("2026-11")
  const u = USAGE[month]
  const own = u.groups.reduce((a, g) => a + g[3], 0)
  const rented = u.groups.reduce((a, g) => a + g[4], 0)
  const jobs = u.groups.reduce((a, g) => a + g[2], 0)
  return (
    <ConsoleFrame>
      <div className="c-body">
        <div className="c-headrow">
          <div>
            <div className="c-h1" style={{ fontSize: 16 }}>
              GPU-hour usage
            </div>
            <p className="c-desc" style={{ fontSize: 12 }}>
              Per person, in UTC months. Own-node jobs are free but count against quotas; rented GPUs are paid for with credit. Quotas and groups are set in Settings → Members.
            </p>
          </div>
          <Select value={month} onValueChange={setMonth}>
            <SelectTrigger className="c-select" aria-label="Month (sample data)">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.keys(USAGE).map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Usage by person, sample data" style={{ marginTop: 12 }}>
          <table className="c-table" style={{ minWidth: 420 }}>
            <caption className="sr-only">Usage by group, sample data</caption>
            <thead>
              <tr>
                <th>Group</th>
                <th className="r">People</th>
                <th className="r">Jobs</th>
                <th className="r">Own GPU-hours</th>
                <th className="r">Rented</th>
              </tr>
            </thead>
            <tbody>
              {u.groups.map(([group, people, j, o, r]) => (
                <tr key={group}>
                  <td>{group}</td>
                  <td className="r num">{people}</td>
                  <td className="r num">{j}</td>
                  <td className="r num">{hours(o)}</td>
                  <td className="r num">{hours(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Usage by group, sample data" style={{ marginTop: 12 }}>
          <table className="c-table" style={{ minWidth: 520 }}>
            <caption className="sr-only">Usage by person, sample data</caption>
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
              {u.people.map(([email, group, used, quota]) => (
                <tr key={email}>
                  <td>{email}</td>
                  <td>{group}</td>
                  <td className="r num">
                    <span className={`meter ${used / quota >= 0.9 ? "hot" : ""}`} aria-hidden="true">
                      <i style={{ width: `${Math.min(100, (used / quota) * 100)}%` }} />
                    </span>
                    {hours(used)}
                  </td>
                  <td className="r num">{quota}</td>
                  <td className="r num">{hours(Math.max(0, quota - used))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="c-note">
          Workspace total in {month}: {hours(own)} own and {hours(rented)} rented GPU-hours across {jobs} jobs.
        </p>
      </div>
    </ConsoleFrame>
  )
}
