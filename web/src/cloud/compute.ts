// Pure helpers for the Compute pages (marketplace, jobs, billing).

import { formatINR } from "@/lib/format"

/** A listing's price. Sharing between partners can be free, and "₹0.00" reads like a bug. */
export function priceLabel(paise: number): string {
  return paise === 0 ? "Free" : formatINR(paise)
}

/** "a, b ,c" → ["a", "b", "c"]: workspace slugs typed by a person, without repeats. */
export function parseSlugs(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean))]
}

/** Split a command line into arguments, honouring single and double quotes. */
export function splitArgs(text: string): string[] {
  const out: string[] = []
  const re = /"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g
  for (let m = re.exec(text); m; m = re.exec(text)) out.push(m[1] !== undefined ? m[1].replace(/\\(.)/g, "$1") : (m[2] ?? m[3]))
  return out
}

/** The inverse of splitArgs: quote what needs it so splitArgs(joinArgs(a)) equals a. */
export function joinArgs(args: string[]): string {
  return args.map((a) => (a !== "" && /^[^\s"'\\]+$/.test(a) ? a : `"${a.replace(/[\\"]/g, "\\$&")}"`)).join(" ")
}

/** NAME=value lines for the environment box. */
export function formatEnv(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n")
}

/** A time limit in seconds as the hours the form asks for ("" for no limit). */
export function secondsToHours(seconds: number): string {
  return seconds > 0 ? String(Number((seconds / 3600).toFixed(4))) : ""
}

/** How to open a running service, from its image: a notebook link or a curl call. Null for anything else. */
export function serviceHint(image: string, endpoint: string, token: string | null): { title: string; code: string } | null {
  if (!endpoint) return null
  const base = endpoint.replace(/\/+$/, "")
  const secret = token ?? "<token>"
  const repo = image.split("@")[0]
  if (/jupyter/i.test(repo)) return { title: "Open JupyterLab in your browser", code: `${base}/lab?token=${secret}` }
  if (/vllm/i.test(repo)) {
    return { title: "Call the model", code: `curl ${base}/v1/models -H "Authorization: Bearer ${secret}"` }
  }
  return null
}

/** KEY=VALUE lines → env map, or the first line that is not one. */
export function parseEnv(text: string): { env: Record<string, string>; bad: string | null } {
  const env: Record<string, string> = {}
  for (const raw of text.split("\n")) {
    const line = raw.trim()
    if (!line) continue
    const eq = line.indexOf("=")
    const name = eq > 0 ? line.slice(0, eq) : ""
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || name.startsWith("MERIDIAN_")) return { env, bad: line }
    env[name] = line.slice(eq + 1)
  }
  return { env, bad: null }
}

/** Why a rental ended, in words. */
export function endReason(reason: string): string {
  return (
    {
      released: "Released",
      provider_ended: "Ended by the provider",
      insufficient_funds: "Ran out of credit",
      job_finished: "Job finished",
      node_lost: "Node went offline",
      sharing_ended: "Listing no longer shared with you",
    }[reason] ?? "Ended"
  )
}

/** How to install the meridian CLI from this control plane (its own wheel, else the installer), never from GitHub. */
export function cliInstallLine(info: { public_url: string; wheel?: string | null }): string {
  return info.wheel ? `pip install ${info.public_url}/download/${info.wheel}` : `curl -fsSL ${info.public_url}/install.sh | bash`
}
