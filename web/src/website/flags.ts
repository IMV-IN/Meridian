/**
 * Install options → the flags `meridian init` takes (meridian/cli/init.py).
 * Only values that differ from init's defaults are emitted, so the command
 * stays short and readable. Secrets are never part of the command: the
 * installer asks for them on the terminal.
 */

export const STRATEGIES = [
  { id: "least_inflight", label: "Least busy", hint: "Send each request to the server with the fewest in flight." },
  { id: "ewma_latency", label: "Fastest recently", hint: "Prefer the server with the lowest recent latency." },
  { id: "token_aware", label: "By request size", hint: "Estimate each request's cost and pick the server that finishes it soonest." },
  { id: "cache_affinity", label: "Reuse cached prompts", hint: "Send repeated prompt prefixes to the server that already has them cached." },
  { id: "weighted_round_robin", label: "Take turns", hint: "Rotate through servers, honouring each one's weight." },
] as const

export const PII_POLICIES = [
  { id: "", label: "Off" },
  { id: "redact_and_replace", label: "Redact before sending to the model" },
  { id: "redact_for_logs", label: "Redact in logs only" },
  { id: "audit_only", label: "Detect and record, change nothing" },
  { id: "block", label: "Reject requests that contain PII" },
] as const

export const ENGINES = ["ollama", "vllm", "sglang", "trtllm", "openai"] as const

export interface BackendRow {
  url: string
  engine: string
  model: string
}

export interface BudgetRow {
  org: string
  tokens: string
}

export interface InstallOptions {
  service: "user" | "system" | "none"
  port: string
  listen: "local" | "all"
  detect: boolean
  backends: BackendRow[]
  strategy: string
  healthInterval: string
  retries: string
  circuitBreaker: boolean
  sessionAffinity: boolean
  tiering: boolean
  auth: "ask" | "on" | "off"
  cloud: boolean
  gatewayName: string
  consoleUrl: string
  keySignIn: boolean
  rateLimit: string
  rateBurst: string
  budgets: BudgetRow[]
  pii: string
  cost: boolean
  currency: string
  auditLog: boolean
}

export const DEFAULTS: InstallOptions = {
  service: "user",
  port: "8080",
  listen: "local",
  detect: true,
  backends: [],
  strategy: "least_inflight",
  healthInterval: "5",
  retries: "0",
  circuitBreaker: false,
  sessionAffinity: false,
  tiering: false,
  auth: "ask",
  cloud: true,
  gatewayName: "",
  consoleUrl: "",
  keySignIn: true,
  rateLimit: "",
  rateBurst: "",
  budgets: [],
  pii: "",
  cost: false,
  currency: "USD",
  auditLog: true,
}

const SAFE = /^[A-Za-z0-9._:/@=,+%-]+$/

/** Quote a value for a POSIX shell only when it needs it. */
export function shellQuote(value: string): string {
  if (value !== "" && SAFE.test(value)) return value
  return `'${value.replace(/'/g, `'\\''`)}'`
}

export const isUrl = (v: string) => /^https?:\/\/[^\s/?#]+(\/[^\s?#]*)?$/.test(v.trim())
export const isName = (v: string) => /^[A-Za-z0-9._-]{1,64}$/.test(v.trim())
const isNumber = (v: string, min: number, max = Infinity) => {
  const n = Number(v)
  return v.trim() !== "" && Number.isFinite(n) && n >= min && n <= max
}

export interface Problem {
  field: string
  message: string
}

/** What would make the command fail; the UI shows these next to the field. */
export function problems(o: InstallOptions): Problem[] {
  const out: Problem[] = []
  if (!isNumber(o.port, 1, 65535) || !Number.isInteger(Number(o.port))) out.push({ field: "port", message: "Use a port from 1 to 65535." })
  if (!isNumber(o.healthInterval, 1, 3600)) out.push({ field: "healthInterval", message: "Use 1 to 3600 seconds." })
  if (!isNumber(o.retries, 0, 5) || !Number.isInteger(Number(o.retries))) out.push({ field: "retries", message: "Use 0 to 5 retries." })
  o.backends.forEach((b, i) => {
    if (!isUrl(b.url)) out.push({ field: `backend-${i}`, message: "Enter a URL like http://127.0.0.1:11434." })
    if (b.model && !/^[^\s,]{1,200}$/.test(b.model)) out.push({ field: `backend-model-${i}`, message: "Model names can't contain spaces or commas." })
  })
  if (o.rateLimit && !isNumber(o.rateLimit, 0.01)) out.push({ field: "rateLimit", message: "Use a positive number of requests per second." })
  if (o.rateBurst && !isNumber(o.rateBurst, 1)) out.push({ field: "rateBurst", message: "Burst must be at least 1." })
  o.budgets.forEach((b, i) => {
    if (!isName(b.org)) out.push({ field: `budget-org-${i}`, message: "Org IDs use letters, digits, dots, dashes and underscores." })
    if (!isNumber(b.tokens, 1)) out.push({ field: `budget-tokens-${i}`, message: "Enter a daily token limit." })
  })
  if (o.cloud && o.gatewayName && !/^[A-Za-z0-9 ._-]{1,80}$/.test(o.gatewayName))
    out.push({ field: "gatewayName", message: "Use letters, digits, spaces, dots and dashes." })
  if (o.cloud && o.consoleUrl && !isUrl(o.consoleUrl)) out.push({ field: "consoleUrl", message: "Enter a URL like http://gw.local:8080." })
  if (o.cost && !/^[A-Z]{3}$/.test(o.currency)) out.push({ field: "currency", message: "Use a three-letter currency code." })
  return out
}

/** `meridian init` flags for these options (valid entries only). */
export function toFlags(o: InstallOptions, cloudUrl: string): string[] {
  const f: string[] = []
  const add = (flag: string, value?: string) => f.push(value === undefined ? flag : `${flag} ${shellQuote(value)}`)
  const bad = new Set(problems(o).map((p) => p.field))

  if (o.port !== DEFAULTS.port && !bad.has("port")) add("--port", String(Number(o.port)))
  if (o.listen !== DEFAULTS.listen) add("--listen", o.listen)
  o.backends.forEach((b, i) => {
    if (bad.has(`backend-${i}`) || bad.has(`backend-model-${i}`)) return
    const parts = [b.url.trim().replace(/\/+$/, ""), `engine=${b.engine}`]
    if (b.model.trim()) parts.push(`model=${b.model.trim()}`)
    add("--backend", parts.join(","))
  })
  if (!o.detect) add("--no-detect")
  if (o.strategy !== DEFAULTS.strategy) add("--strategy", o.strategy)
  if (o.healthInterval !== DEFAULTS.healthInterval && !bad.has("healthInterval")) add("--health-interval", String(Number(o.healthInterval)))
  if (o.retries !== DEFAULTS.retries && !bad.has("retries")) add("--retries", String(Number(o.retries)))
  if (o.circuitBreaker) add("--circuit-breaker")
  if (o.sessionAffinity) add("--session-affinity")
  if (o.tiering) add("--tiering")
  if (o.rateLimit && !bad.has("rateLimit")) {
    add("--rate-limit", String(Number(o.rateLimit)))
    if (o.rateBurst && !bad.has("rateBurst")) add("--rate-burst", String(Number(o.rateBurst)))
  }
  o.budgets.forEach((b, i) => {
    if (!bad.has(`budget-org-${i}`) && !bad.has(`budget-tokens-${i}`)) add("--budget", `${b.org.trim()}=${Number(b.tokens)}`)
  })
  if (o.pii) add("--pii", o.pii)
  if (o.cost) {
    add("--cost")
    if (o.currency !== DEFAULTS.currency && !bad.has("currency")) add("--currency", o.currency)
  }
  if (!o.auditLog) add("--no-audit-log")
  if (o.auth === "on") add("--auth")
  if (o.auth === "off") add("--no-auth")
  if (o.cloud) {
    add("--cloud", cloudUrl)
    if (o.gatewayName.trim() && !bad.has("gatewayName")) add("--gateway-name", o.gatewayName.trim())
    if (o.consoleUrl.trim() && !bad.has("consoleUrl")) add("--console-url", o.consoleUrl.trim().replace(/\/+$/, ""))
    if (!o.keySignIn) add("--no-key-sign-in")
  }
  add("--service", o.service)
  return f
}

/** The one-liner; with several flags, one per line (`\` continuations paste fine into bash and zsh). */
export function installCommand(o: InstallOptions, origin: string): string {
  const flags = toFlags(o, origin)
  const base = `curl -fsSL ${origin}/install.sh | bash`
  if (!flags.length) return base
  if (flags.length === 1) return `${base} -s -- ${flags[0]}`
  return `${base} -s -- \\\n` + flags.map((f) => `  ${f}`).join(" \\\n")
}

/** What the installer will still ask on the terminal. */
export function questions(o: InstallOptions): string[] {
  const q: string[] = []
  if (o.cloud) q.push("Your Meridian Cloud gateway token (hidden as you paste it)")
  if (o.cloud && !o.gatewayName.trim()) q.push("A name for the gateway (defaults to the computer's name)")
  if (o.cloud && !o.consoleUrl.trim()) q.push("The address people open its console at")
  if (o.detect) q.push("Which model servers it found to route to")
  if (!o.backends.length) q.push("A model server URL, if none is found")
  if (o.backends.some((b) => !b.model.trim()) || o.detect) q.push("Which model each server should serve, when it has several")
  if (o.auth === "ask" && !o.budgets.length && !o.cost) q.push("Whether to require API keys")
  return q
}
