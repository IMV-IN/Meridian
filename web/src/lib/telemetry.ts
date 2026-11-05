// Client-side live telemetry. The gateway exposes cumulative Prometheus
// counters and a point-in-time /meridian/status; the console samples both on
// each refresh and derives rates (Δcounter/Δt), windowed latency quantiles
// (Δhistogram) and change events (health flips, circuit trips, canary moves).
// Pure functions — the provider owns the timers and ring buffers.

import type { BackendStatus, GatewayStatus } from "./api/types"
import { diffHistogram, histogram, quantile, sum, sumBy, type Exposition, type Histogram } from "./prometheus"

export interface CounterSnapshot {
  t: number
  byClass: { "2xx": number; "4xx": number; "5xx": number; other: number }
  byBackend: Record<string, number>
  latency: Histogram
  tokens: number
}

export function snapshotFromMetrics(exp: Exposition, t: number): CounterSnapshot {
  const byStatus = sumBy(exp, "meridian_requests_total", "status")
  const byClass = { "2xx": 0, "4xx": 0, "5xx": 0, other: 0 }
  for (const [code, n] of Object.entries(byStatus)) {
    const c = Number(code)
    if (c >= 200 && c < 300) byClass["2xx"] += n
    else if (c >= 400 && c < 500) byClass["4xx"] += n
    else if (c >= 500 && c < 600) byClass["5xx"] += n
    else byClass.other += n
  }
  return {
    t,
    byClass,
    byBackend: sumBy(exp, "meridian_requests_total", "backend"),
    latency: histogram(exp, "meridian_request_latency_ms"),
    tokens: sum(exp, "meridian_tokens_total"),
  }
}

export interface TrafficPoint {
  t: number
  rps: number
  rps2xx: number
  rps4xx: number
  rps5xx: number
  /** 5xx share of requests in the interval; null when idle. */
  errorRate: number | null
  p50: number | null
  p95: number | null
  tokensPerSec: number
  byBackend: Record<string, number>
}

const nonNeg = (v: number) => (v > 0 ? v : 0)

/**
 * Rate point between two cumulative snapshots. A counter that went backwards
 * means the gateway restarted — the point is computed from the new absolute
 * values so the chart never shows a negative spike.
 */
export function derivePoint(prev: CounterSnapshot, cur: CounterSnapshot): TrafficPoint | null {
  const dt = (cur.t - prev.t) / 1000
  if (dt <= 0) return null
  const prevTotal = prev.byClass["2xx"] + prev.byClass["4xx"] + prev.byClass["5xx"] + prev.byClass.other
  const curTotal = cur.byClass["2xx"] + cur.byClass["4xx"] + cur.byClass["5xx"] + cur.byClass.other
  const reset = curTotal < prevTotal
  const base = reset ? null : prev
  const d = (k: keyof CounterSnapshot["byClass"]) => nonNeg(cur.byClass[k] - (base ? base.byClass[k] : 0))
  const d2 = d("2xx")
  const d4 = d("4xx")
  const d5 = d("5xx")
  const dAll = d2 + d4 + d5 + d("other")
  const window = diffHistogram(base ? base.latency : null, cur.latency)
  const byBackend: Record<string, number> = {}
  for (const [name, v] of Object.entries(cur.byBackend)) {
    byBackend[name] = nonNeg(v - (base?.byBackend[name] ?? 0)) / dt
  }
  return {
    t: cur.t,
    rps: dAll / dt,
    rps2xx: d2 / dt,
    rps4xx: d4 / dt,
    rps5xx: d5 / dt,
    errorRate: dAll > 0 ? d5 / dAll : null,
    p50: quantile(0.5, window),
    p95: quantile(0.95, window),
    tokensPerSec: nonNeg(cur.tokens - (base?.tokens ?? 0)) / dt,
    byBackend,
  }
}

export interface BackendPoint {
  t: number
  ewma: number
  inflight: number
  queue: number | null
}

export type EventSeverity = "good" | "info" | "warning" | "critical"

export interface ConsoleEvent {
  id: string
  t: number
  severity: EventSeverity
  title: string
  detail?: string
  href?: string
}

let eventSeq = 0
function evt(t: number, severity: EventSeverity, title: string, detail?: string, href?: string): ConsoleEvent {
  eventSeq += 1
  return { id: `${t}-${eventSeq}`, t, severity, title, detail, href }
}

function backendHref(b: BackendStatus) {
  return `/backends/${encodeURIComponent(b.name)}`
}

/** Change events between two status snapshots (operator activity feed). */
export function diffStatus(prev: GatewayStatus | null, cur: GatewayStatus, t: number): ConsoleEvent[] {
  if (!prev) return []
  const out: ConsoleEvent[] = []
  const before = new Map(prev.backends.map((b) => [b.name, b]))
  const after = new Map(cur.backends.map((b) => [b.name, b]))

  for (const b of cur.backends) {
    const p = before.get(b.name)
    if (!p) {
      out.push(evt(t, "info", `Backend ${b.name} registered`, `${b.engine} · ${b.model || "any model"}`, backendHref(b)))
      continue
    }
    if (p.healthy && !b.healthy)
      out.push(evt(t, "critical", `Backend ${b.name} is unhealthy`, "Failed active health checks; removed from routing.", backendHref(b)))
    if (!p.healthy && b.healthy)
      out.push(evt(t, "good", `Backend ${b.name} recovered`, "Passing health checks; back in rotation.", backendHref(b)))
    if (!p.idle && b.idle)
      out.push(evt(t, "info", `Backend ${b.name} scaled to zero`, "Idle timeout elapsed; excluded until woken.", backendHref(b)))
    if (p.idle && !b.idle) out.push(evt(t, "info", `Backend ${b.name} woke up`, undefined, backendHref(b)))
    const pc = p.circuit?.state
    const cc = b.circuit?.state
    if (pc !== cc && cc === "open")
      out.push(evt(t, "critical", `Circuit opened on ${b.name}`, `${b.circuit?.consecutive_failures ?? 0} consecutive failures — requests rejected pre-flight.`, backendHref(b)))
    if (pc === "open" && cc === "half_open")
      out.push(evt(t, "warning", `Circuit half-open on ${b.name}`, "Admitting a single probe request.", backendHref(b)))
    if (pc && pc !== "closed" && cc === "closed")
      out.push(evt(t, "good", `Circuit closed on ${b.name}`, "Probe succeeded.", backendHref(b)))
  }
  for (const p of prev.backends) {
    if (!after.has(p.name)) out.push(evt(t, "warning", `Backend ${p.name} deregistered`, undefined, "/backends"))
  }

  const pc = prev.canary
  const cc = cur.canary
  if (pc && cc) {
    if (!pc.rolled_back && cc.rolled_back)
      out.push(
        evt(
          t,
          "critical",
          "Canary rolled back",
          `Error rate ${cc.window_error_rate !== null ? `${(cc.window_error_rate * 100).toFixed(1)}%` : "—"} breached the threshold; weight forced to 0%.`,
          "/routing",
        ),
      )
    else if (pc.weight !== cc.weight)
      out.push(evt(t, "info", `Canary weight ${pc.weight}% → ${cc.weight}%`, `Step ${cc.step + 1}`, "/routing"))
    if (!pc.schedule_done && cc.schedule_done && !cc.rolled_back)
      out.push(evt(t, "good", "Canary schedule complete", `Holding ${cc.weight}% on the canary pool.`, "/routing"))
  }
  return out
}

/** Health classification shared by tables, badges and counts. */
export type BackendHealth = "healthy" | "degraded" | "unhealthy" | "idle" | "circuit_open"

export function backendHealth(b: BackendStatus): BackendHealth {
  if (!b.healthy) return "unhealthy"
  if (b.circuit?.state === "open") return "circuit_open"
  if (b.idle) return "idle"
  if (b.circuit?.state === "half_open") return "degraded"
  return "healthy"
}
