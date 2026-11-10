// Pure helpers for the Compute pages (marketplace, jobs, billing).

/** Split a command line into arguments, honouring single and double quotes. */
export function splitArgs(text: string): string[] {
  const out: string[] = []
  const re = /"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+)/g
  for (let m = re.exec(text); m; m = re.exec(text)) out.push(m[1] !== undefined ? m[1].replace(/\\(.)/g, "$1") : (m[2] ?? m[3]))
  return out
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
    }[reason] ?? "Ended"
  )
}
