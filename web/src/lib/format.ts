import { formatDistanceToNowStrict } from "date-fns"

/**
 * Numbers and dates follow the viewer's own locale (navigator.languages), not
 * a hard-coded one: an operator in India sees 1,00,000; one in Germany 100.000.
 */
export const LOCALE: string | undefined =
  typeof navigator !== "undefined" ? (navigator.languages?.[0] ?? navigator.language) : undefined

const compactFmt = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 })
const intFmt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })

/** 1,284 / 12.9K / 4.2M — auto-compacts at 10,000. */
export function formatNumber(v: number | null | undefined, opts: { compact?: boolean; digits?: number } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  if (!Number.isFinite(v)) return v > 0 ? "∞" : "−∞"
  const compact = opts.compact ?? Math.abs(v) >= 10_000
  if (compact) return compactFmt.format(v)
  if (opts.digits !== undefined) {
    return new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: opts.digits,
      maximumFractionDigits: opts.digits,
    }).format(v)
  }
  return Number.isInteger(v) ? intFmt.format(v) : new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 }).format(v)
}

/** Latency: 0.8 ms / 42 ms / 1.24 s / 2.1 min */
export function formatMs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  if (v < 1) return `${v.toFixed(2)} ms`
  if (v < 1000) return `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`
  if (v < 60_000) return `${(v / 1000).toFixed(v < 10_000 ? 2 : 1)} s`
  return `${(v / 60_000).toFixed(1)} min`
}

/** Durations in seconds: 45s / 5m / 2h 10m / 3d */
export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—"
  if (!Number.isFinite(s)) return "∞"
  if (s < 1) return `${Math.round(s * 1000)}ms`
  if (s < 60) return `${Number.isInteger(s) ? s : s.toFixed(1)}s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const r = Math.round(s % 60)
    return r ? `${m}m ${r}s` : `${m}m`
  }
  if (s < 86_400) {
    const h = Math.floor(s / 3600)
    const m = Math.round((s % 3600) / 60)
    return m ? `${h}h ${m}m` : `${h}h`
  }
  const d = Math.floor(s / 86_400)
  const h = Math.round((s % 86_400) / 3600)
  return h ? `${d}d ${h}h` : `${d}d`
}

export function formatPercent(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  const pct = v * 100
  if (pct !== 0 && Math.abs(pct) < 0.1) return "<0.1%"
  return `${pct.toFixed(pct === 100 || pct === 0 ? 0 : digits)}%`
}

/** Indian rupees from integer paise, with Indian digit grouping: 1234567 → ₹12,345.67. */
export function formatINR(paise: number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (paise === null || paise === undefined || Number.isNaN(paise)) return "—"
  const abs = Math.abs(Math.trunc(paise))
  const whole = Math.floor(abs / 100).toLocaleString("en-IN")
  const sign = paise < 0 ? "−" : opts.signed && paise > 0 ? "+" : ""
  return `${sign}₹${whole}.${String(abs % 100).padStart(2, "0")}`
}

/** Meridian's fee on an amount of paise, rounded half up: matches meridian_control.compute.fee_for. */
export function feeFor(paise: number, bps = 200): number {
  return Math.floor((paise * bps + 5000) / 10000)
}

/** What a provider has earned after `seconds`, in paise, rounded down: matches meridian_control.compute.rental_due. */
export function rentalDue(pricePaisePerGpuHour: number, gpus: number, seconds: number): number {
  return Math.floor((pricePaisePerGpuHour * gpus * Math.max(0, seconds)) / 3600)
}

/** VRAM in whole GB, the way GPUs are sold: 85899345920 → "80 GB". */
export function formatVram(bytes: number | null | undefined): string {
  if (!bytes) return "—"
  return `${Math.round(bytes / 1024 ** 3)} GB`
}

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
export function formatBytes(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  let n = Math.abs(v)
  let u = 0
  while (n >= 1024 && u < UNITS.length - 1) {
    n /= 1024
    u++
  }
  const sign = v < 0 ? "-" : ""
  return `${sign}${u === 0 ? n : n.toFixed(digits)} ${UNITS[u]}`
}

export function formatCurrency(v: number | null | undefined, currency = "USD"): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  const digits = Math.abs(v) > 0 && Math.abs(v) < 1 ? 4 : 2
  try {
    return new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(v)
  } catch {
    return `${v.toFixed(digits)} ${currency}`
  }
}

export type TimeZoneMode = "local" | "utc"

export function formatTimestamp(
  iso: string | number | Date | null | undefined,
  tz: TimeZoneMode = "local",
  opts: { seconds?: boolean; date?: boolean } = {},
): string {
  if (iso === null || iso === undefined) return "—"
  const d = iso instanceof Date ? iso : new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  const { seconds = true, date = true } = opts
  const parts: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
    ...(date ? { month: "short", day: "numeric" } : {}),
    hour12: false,
    timeZone: tz === "utc" ? "UTC" : undefined,
  }
  const s = new Intl.DateTimeFormat(LOCALE, parts).format(d)
  return tz === "utc" ? `${s} UTC` : s
}

export function formatRelative(iso: string | number | Date | null | undefined): string {
  if (iso === null || iso === undefined) return "—"
  const d = iso instanceof Date ? iso : new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  const diff = d.getTime() - Date.now()
  if (Math.abs(diff) < 5_000) return diff >= 0 ? "in a moment" : "just now"
  const s = formatDistanceToNowStrict(d)
  return diff > 0 ? `in ${s}` : `${s} ago`
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

export function pluralize(n: number, word: string, plural = `${word}s`): string {
  return `${formatNumber(n)} ${n === 1 ? word : plural}`
}

export function shortId(id: string | null | undefined, keep = 10): string {
  if (!id) return "—"
  return id.length > keep + 2 ? `${id.slice(0, keep)}…` : id
}

/** HTTP status → class bucket used for colors and grouping. */
export function statusClass(code: number): "2xx" | "3xx" | "4xx" | "5xx" | "other" {
  if (code >= 200 && code < 300) return "2xx"
  if (code >= 300 && code < 400) return "3xx"
  if (code >= 400 && code < 500) return "4xx"
  if (code >= 500 && code < 600) return "5xx"
  return "other"
}

export function percentile(sorted: number[], p: number): number | null {
  if (!sorted.length) return null
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1))
  return sorted[idx]
}

/** Compact latency for axis ticks: 750ms / 1.5s */
export function formatMsShort(v: number): string {
  if (!Number.isFinite(v)) return ""
  if (v < 1000) return `${Math.round(v)}ms`
  const s = v / 1000
  return `${Number.isInteger(s) ? s : s.toFixed(1)}s`
}

/**
 * Managed backends are named `managed:{node_id}:{engine_id}` by the
 * projection sync — show the engine first and the node as context.
 */
export function backendLabel(name: string): { primary: string; secondary: string | null; managed: boolean } {
  if (name.startsWith("managed:")) {
    const [, node = "", engine = ""] = name.split(":")
    return { primary: engine || name, secondary: `node ${shortId(node, 14)}`, managed: true }
  }
  return { primary: name, secondary: null, managed: false }
}

/** EWMA latency: 0 means no completed request yet. */
export function formatEwma(v: number | null | undefined): string {
  return v ? formatMs(v) : "—"
}
