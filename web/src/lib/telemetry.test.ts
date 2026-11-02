import { describe, expect, it } from "vitest"

import type { BackendStatus, GatewayStatus } from "./api/types"
import type { Histogram } from "./prometheus"
import { backendHealth, derivePoint, diffStatus, type CounterSnapshot } from "./telemetry"

const h = (c10: number, c100: number, inf: number): Histogram => ({
  buckets: [
    { le: 10, count: c10 },
    { le: 100, count: c100 },
    { le: Number.POSITIVE_INFINITY, count: inf },
  ],
  count: inf,
  sum: 0,
})

function snap(t: number, ok: number, c4: number, c5: number, latency: Histogram, byBackend: Record<string, number> = {}): CounterSnapshot {
  return { t, byClass: { "2xx": ok, "4xx": c4, "5xx": c5, other: 0 }, byBackend, latency, tokens: 0 }
}

describe("derivePoint", () => {
  it("computes per-second rates and the windowed error rate", () => {
    const a = snap(0, 100, 0, 0, h(0, 100, 100), { fast: 100 })
    const b = snap(2000, 116, 2, 2, h(0, 120, 120), { fast: 120 })
    const p = derivePoint(a, b)!
    expect(p.rps).toBe(10) // 20 requests / 2s
    expect(p.rps2xx).toBe(8)
    expect(p.rps5xx).toBe(1)
    expect(p.errorRate).toBeCloseTo(0.1)
    expect(p.byBackend.fast).toBe(10)
    // All 20 windowed observations fall in (10, 100].
    expect(p.p50).toBe(55)
  })

  it("never emits negative rates when the gateway restarts (counter reset)", () => {
    const before = snap(0, 5000, 10, 10, h(0, 5000, 5020))
    const after = snap(2000, 4, 0, 0, h(0, 4, 4))
    const p = derivePoint(before, after)!
    expect(p.rps).toBe(2)
    expect(p.rps2xx).toBe(2)
    expect(p.errorRate).toBe(0)
  })

  it("reports null error rate when idle and skips non-advancing time", () => {
    const a = snap(0, 1, 0, 0, h(0, 1, 1))
    expect(derivePoint(a, snap(1000, 1, 0, 0, h(0, 1, 1)))!.errorRate).toBeNull()
    expect(derivePoint(a, snap(0, 2, 0, 0, h(0, 2, 2)))).toBeNull()
  })
})

function backend(over: Partial<BackendStatus> = {}): BackendStatus {
  return {
    name: "fast",
    url: "http://x",
    engine: "vllm",
    model: "m",
    weight: 1,
    tags: [],
    healthy: true,
    idle: false,
    inflight: 0,
    inflight_cost: 0,
    ewma_latency_ms: 10,
    queue_depth: null,
    tokens_per_sec: null,
    gpu_mem_util: null,
    circuit: { state: "closed", consecutive_failures: 0 },
    ...over,
  }
}

const status = (backends: BackendStatus[], canary?: GatewayStatus["canary"]): GatewayStatus => ({
  strategy: "least_inflight",
  backends,
  canary,
})

describe("diffStatus", () => {
  it("is silent on the first snapshot", () => {
    expect(diffStatus(null, status([backend()]), 1)).toEqual([])
  })

  it("reports health flips, circuit trips and registration changes", () => {
    const prev = status([backend(), backend({ name: "old" })])
    const cur = status([
      backend({ healthy: false, circuit: { state: "open", consecutive_failures: 5 } }),
      backend({ name: "new" }),
    ])
    const titles = diffStatus(prev, cur, 1).map((e) => `${e.severity}:${e.title}`)
    expect(titles).toContain("critical:Backend fast is unhealthy")
    expect(titles).toContain("critical:Circuit opened on fast")
    expect(titles).toContain("info:Backend new registered")
    expect(titles).toContain("warning:Backend old deregistered")
  })

  it("reports recovery and canary rollback", () => {
    const canary = {
      enabled: true,
      weight: 25,
      step: 1,
      rolled_back: false,
      schedule_done: false,
      window_samples: 30,
      window_error_rate: 0.1,
      canary_tags: ["canary"],
      stable_tags: ["stable"],
    }
    const prev = status([backend({ healthy: false })], canary)
    const cur = status([backend()], { ...canary, weight: 0, rolled_back: true, window_error_rate: 0.6 })
    const events = diffStatus(prev, cur, 1)
    expect(events.find((e) => e.title === "Backend fast recovered")?.severity).toBe("good")
    const rb = events.find((e) => e.title === "Canary rolled back")
    expect(rb?.severity).toBe("critical")
    expect(rb?.detail).toContain("60.0%")
  })
})

describe("backendHealth", () => {
  it("prioritizes unhealthy > circuit open > idle > half-open", () => {
    expect(backendHealth(backend({ healthy: false, idle: true }))).toBe("unhealthy")
    expect(backendHealth(backend({ circuit: { state: "open", consecutive_failures: 3 }, idle: true }))).toBe("circuit_open")
    expect(backendHealth(backend({ idle: true }))).toBe("idle")
    expect(backendHealth(backend({ circuit: { state: "half_open", consecutive_failures: 0 } }))).toBe("degraded")
    expect(backendHealth(backend({ circuit: null }))).toBe("healthy")
  })
})
