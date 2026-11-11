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

/** A service endpoint as a plain `http(s)://host:port` origin, or null when it is anything else
 * (credentials, a path, a query, whitespace or shell characters). The server already builds endpoints
 * from validated parts; this keeps a copyable snippet safe even if one is not. */
export function serviceEndpoint(endpoint: string): string | null {
  if (!endpoint || /[^\x21-\x7e]/.test(endpoint)) return null
  let u: URL
  try {
    u = new URL(endpoint)
  } catch {
    return null
  }
  if ((u.protocol !== "http:" && u.protocol !== "https:") || u.username || u.password || u.search || u.hash) return null
  if (u.pathname !== "/" || !/^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)$/.test(u.hostname)) return null
  return u.origin
}

/** A value as one single-quoted POSIX shell word. */
export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`
}

/** A value as a YAML double-quoted scalar (a JSON string is one). */
export function yamlQuote(value: string): string {
  return JSON.stringify(value)
}

/** How to open a running service, from its image: a notebook link or a curl call. Null for anything else. */
export function serviceHint(image: string, endpoint: string, token: string | null): { title: string; code: string } | null {
  const base = serviceEndpoint(endpoint)
  if (!base) return null
  const secret = token ?? "<token>"
  const repo = image.split("@")[0]
  if (/jupyter/i.test(repo)) return { title: "Open JupyterLab in your browser", code: `${base}/lab?token=${secret}` }
  if (/vllm/i.test(repo)) {
    return { title: "Call the model", code: `curl ${shellQuote(`${base}/v1/models`)} -H ${shellQuote(`Authorization: Bearer ${secret}`)}` }
  }
  return null
}

// Set by Meridian or read by the NVIDIA container runtime; the server refuses them too
// (meridian_control/validation.py RESERVED_ENV).
const RESERVED_ENV = new Set([
  "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "NVIDIA_DISABLE_REQUIRE",
  "NVIDIA_MIG_CONFIG_DEVICES", "NVIDIA_MIG_MONITOR_DEVICES", "NVIDIA_IMEX_CHANNELS",
])
const RESERVED_ENV_PREFIXES = ["MERIDIAN_", "NVIDIA_REQUIRE_", "CUDA_MPS_"]

export function reservedEnv(name: string): boolean {
  return RESERVED_ENV.has(name) || RESERVED_ENV_PREFIXES.some((p) => name.startsWith(p))
}

/** KEY=VALUE lines → env map, or the first line that is not one. */
export function parseEnv(text: string): { env: Record<string, string>; bad: string | null } {
  const env: Record<string, string> = {}
  for (const raw of text.split("\n")) {
    const line = raw.trim()
    if (!line) continue
    const eq = line.indexOf("=")
    const name = eq > 0 ? line.slice(0, eq) : ""
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name) || reservedEnv(name)) return { env, bad: line }
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
