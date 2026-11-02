// Minimal Prometheus text-exposition parser + aggregation helpers for the
// console. Handles escaped label values, +Inf/NaN, and optional timestamps.
// `*_created` series (prometheus_client bookkeeping) are dropped.

export interface Sample {
  name: string
  labels: Record<string, string>
  value: number
}

export interface MetricFamily {
  name: string
  type: string
  help: string
}

export interface Exposition {
  samples: Sample[]
  families: Map<string, MetricFamily>
}

function parseValue(raw: string): number {
  if (raw === "+Inf" || raw === "Inf") return Number.POSITIVE_INFINITY
  if (raw === "-Inf") return Number.NEGATIVE_INFINITY
  if (raw === "NaN") return Number.NaN
  return Number(raw)
}

function parseLabels(src: string): Record<string, string> {
  const labels: Record<string, string> = {}
  let i = 0
  while (i < src.length) {
    while (i < src.length && (src[i] === "," || src[i] === " ")) i++
    if (i >= src.length) break
    const eq = src.indexOf("=", i)
    if (eq === -1) break
    const key = src.slice(i, eq).trim()
    i = eq + 1
    if (src[i] !== '"') break
    i++
    let val = ""
    while (i < src.length && src[i] !== '"') {
      if (src[i] === "\\" && i + 1 < src.length) {
        const nxt = src[i + 1]
        val += nxt === "n" ? "\n" : nxt
        i += 2
      } else {
        val += src[i]
        i++
      }
    }
    i++ // closing quote
    labels[key] = val
  }
  return labels
}

export function parsePrometheus(text: string): Exposition {
  const samples: Sample[] = []
  const families = new Map<string, MetricFamily>()
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim()
    if (!line) continue
    if (line.startsWith("#")) {
      const m = /^#\s+(HELP|TYPE)\s+(\S+)\s*(.*)$/.exec(line)
      if (!m) continue
      const [, kind, name, rest] = m
      const fam = families.get(name) ?? { name, type: "untyped", help: "" }
      if (kind === "HELP") fam.help = rest.replace(/\\n/g, "\n").replace(/\\\\/g, "\\")
      else fam.type = rest.trim()
      families.set(name, fam)
      continue
    }
    let name: string
    let labels: Record<string, string> = {}
    let rest: string
    const brace = line.indexOf("{")
    const space = line.indexOf(" ")
    if (brace !== -1 && (space === -1 || brace < space)) {
      name = line.slice(0, brace)
      // Find the closing brace that is not inside a quoted value.
      let j = brace + 1
      let inQuote = false
      for (; j < line.length; j++) {
        const c = line[j]
        if (c === "\\" && inQuote) {
          j++
          continue
        }
        if (c === '"') inQuote = !inQuote
        else if (c === "}" && !inQuote) break
      }
      labels = parseLabels(line.slice(brace + 1, j))
      rest = line.slice(j + 1).trim()
    } else {
      name = line.slice(0, space)
      rest = line.slice(space + 1).trim()
    }
    if (name.endsWith("_created")) continue
    const value = parseValue(rest.split(/\s+/)[0] ?? "")
    if (Number.isNaN(value) && rest.split(/\s+/)[0] !== "NaN") continue
    samples.push({ name, labels, value })
  }
  return { samples, families }
}

export type LabelFilter = Record<string, string | ((v: string) => boolean)>

function matches(labels: Record<string, string>, filter?: LabelFilter): boolean {
  if (!filter) return true
  for (const [k, want] of Object.entries(filter)) {
    const have = labels[k] ?? ""
    if (typeof want === "function" ? !want(have) : have !== want) return false
  }
  return true
}

export function select(exp: Exposition, name: string, filter?: LabelFilter): Sample[] {
  return exp.samples.filter((s) => s.name === name && matches(s.labels, filter))
}

export function sum(exp: Exposition, name: string, filter?: LabelFilter): number {
  return select(exp, name, filter).reduce((acc, s) => acc + (Number.isFinite(s.value) ? s.value : 0), 0)
}

/** Sum grouped by one label: `{ [labelValue]: total }`. */
export function sumBy(exp: Exposition, name: string, label: string, filter?: LabelFilter): Record<string, number> {
  const out: Record<string, number> = {}
  for (const s of select(exp, name, filter)) {
    const k = s.labels[label] ?? ""
    out[k] = (out[k] ?? 0) + (Number.isFinite(s.value) ? s.value : 0)
  }
  return out
}

export interface HistogramBucket {
  le: number
  count: number // cumulative
}

export interface Histogram {
  buckets: HistogramBucket[]
  count: number
  sum: number
}

/** Aggregate a histogram family across all series matching `filter`. */
export function histogram(exp: Exposition, base: string, filter?: LabelFilter): Histogram {
  const byLe = new Map<number, number>()
  for (const s of select(exp, `${base}_bucket`, filter)) {
    const le = parseValue(s.labels.le ?? "+Inf")
    byLe.set(le, (byLe.get(le) ?? 0) + s.value)
  }
  const buckets = [...byLe.entries()].map(([le, count]) => ({ le, count })).sort((a, b) => a.le - b.le)
  return {
    buckets,
    count: sum(exp, `${base}_count`, filter),
    sum: sum(exp, `${base}_sum`, filter),
  }
}

/** Subtract two cumulative histograms (b − a) — for windowed quantiles. */
export function diffHistogram(a: Histogram | null, b: Histogram): Histogram {
  if (!a) return b
  const prev = new Map(a.buckets.map((x) => [x.le, x.count]))
  const buckets = b.buckets.map((x) => ({ le: x.le, count: Math.max(0, x.count - (prev.get(x.le) ?? 0)) }))
  return { buckets, count: Math.max(0, b.count - a.count), sum: Math.max(0, b.sum - a.sum) }
}

/**
 * Estimate a quantile with Prometheus `histogram_quantile` semantics (linear
 * interpolation inside the bucket; the +Inf bucket returns the last finite
 * upper bound). Returns null when there are no observations.
 */
export function quantile(q: number, h: Histogram): number | null {
  const buckets = h.buckets
  if (!buckets.length) return null
  const total = buckets[buckets.length - 1].count
  if (total <= 0) return null
  const rank = q * total
  let prevLe = 0
  let prevCount = 0
  for (const b of buckets) {
    if (b.count >= rank) {
      if (!Number.isFinite(b.le)) return prevLe
      const inBucket = b.count - prevCount
      if (inBucket <= 0) return b.le
      return prevLe + ((b.le - prevLe) * (rank - prevCount)) / inBucket
    }
    prevLe = Number.isFinite(b.le) ? b.le : prevLe
    prevCount = b.count
  }
  return prevLe
}

/** Per-bucket (non-cumulative) counts for a distribution chart. */
export function bucketCounts(h: Histogram): { label: string; le: number; count: number }[] {
  let prev = 0
  let prevLe = 0
  return h.buckets.map((b) => {
    const count = Math.max(0, b.count - prev)
    prev = b.count
    const label = Number.isFinite(b.le) ? `${formatLe(prevLe)}–${formatLe(b.le)}` : `>${formatLe(prevLe)}`
    if (Number.isFinite(b.le)) prevLe = b.le
    return { label, le: b.le, count }
  })
}

function formatLe(v: number): string {
  return v >= 1000 ? `${v / 1000}s` : `${v}`
}
