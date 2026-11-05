import { describe, expect, it } from "vitest"

import { bucketCounts, diffHistogram, histogram, parsePrometheus, quantile, select, sum, sumBy } from "./prometheus"

const TEXT = `# HELP meridian_requests_total Total requests proxied
# TYPE meridian_requests_total counter
meridian_requests_total{backend="fast",model="m",status="200",stream="false"} 90.0
meridian_requests_total{backend="fast",model="m",status="500",stream="false"} 10.0
meridian_requests_total{backend="slow",model="m",status="200",stream="true"} 50.0
# HELP meridian_requests_created Total requests proxied
# TYPE meridian_requests_created gauge
meridian_requests_created{backend="fast",model="m",status="200",stream="false"} 1.79e+09
# TYPE meridian_request_latency_ms histogram
meridian_request_latency_ms_bucket{backend="fast",le="10.0",model="m"} 0.0
meridian_request_latency_ms_bucket{backend="fast",le="100.0",model="m"} 50.0
meridian_request_latency_ms_bucket{backend="fast",le="1000.0",model="m"} 100.0
meridian_request_latency_ms_bucket{backend="fast",le="+Inf",model="m"} 100.0
meridian_request_latency_ms_count{backend="fast",model="m"} 100.0
meridian_request_latency_ms_sum{backend="fast",model="m"} 25000.0
meridian_canary_weight 25.0
weird_metric{path="a\\"b,c",note="x\\ny"} 1 1700000000000
`

describe("parsePrometheus", () => {
  const exp = parsePrometheus(TEXT)

  it("parses samples, drops *_created bookkeeping series", () => {
    expect(select(exp, "meridian_requests_created")).toHaveLength(0)
    expect(select(exp, "meridian_requests_total")).toHaveLength(3)
    expect(exp.families.get("meridian_requests_total")?.type).toBe("counter")
    expect(exp.families.get("meridian_requests_total")?.help).toBe("Total requests proxied")
  })

  it("handles unlabeled samples, escaped label values and timestamps", () => {
    expect(select(exp, "meridian_canary_weight")[0].value).toBe(25)
    const w = select(exp, "weird_metric")[0]
    expect(w.labels).toEqual({ path: 'a"b,c', note: "x\ny" })
    expect(w.value).toBe(1)
  })

  it("aggregates with sum / sumBy and label filters", () => {
    expect(sum(exp, "meridian_requests_total")).toBe(150)
    expect(sum(exp, "meridian_requests_total", { backend: "fast" })).toBe(100)
    expect(sumBy(exp, "meridian_requests_total", "status")).toEqual({ "200": 140, "500": 10 })
    expect(sum(exp, "meridian_requests_total", { status: (s) => s.startsWith("5") })).toBe(10)
  })
})

describe("histogram math", () => {
  const exp = parsePrometheus(TEXT)
  const h = histogram(exp, "meridian_request_latency_ms")

  it("builds sorted cumulative buckets including +Inf", () => {
    expect(h.buckets.map((b) => b.le)).toEqual([10, 100, 1000, Number.POSITIVE_INFINITY])
    expect(h.count).toBe(100)
    expect(h.sum).toBe(25000)
  })

  it("interpolates quantiles like histogram_quantile", () => {
    // 50 of 100 observations fall in (10, 100] → p50 is exactly the bucket top.
    expect(quantile(0.5, h)).toBe(100)
    // p75: rank 75 lies halfway through (100, 1000] → 550.
    expect(quantile(0.75, h)).toBe(550)
    // p25: rank 25 lies halfway through (10, 100] → 55.
    expect(quantile(0.25, h)).toBe(55)
  })

  it("returns null with no observations and caps +Inf at the last finite bound", () => {
    expect(quantile(0.5, { buckets: [], count: 0, sum: 0 })).toBeNull()
    const tail = { buckets: [{ le: 10, count: 0 }, { le: Number.POSITIVE_INFINITY, count: 5 }], count: 5, sum: 100 }
    expect(quantile(0.99, tail)).toBe(10)
  })

  it("diffs cumulative histograms for windowed quantiles", () => {
    const later = {
      buckets: h.buckets.map((b) => ({ le: b.le, count: b.count + (b.le >= 1000 ? 10 : 0) })),
      count: 110,
      sum: 30000,
    }
    const window = diffHistogram(h, later)
    expect(window.count).toBe(10)
    expect(window.buckets.find((b) => b.le === 100)?.count).toBe(0)
    expect(quantile(0.5, window)).toBe(550)
  })

  it("converts to per-bucket counts with readable labels", () => {
    const counts = bucketCounts(h)
    expect(counts.map((c) => c.count)).toEqual([0, 50, 50, 0])
    expect(counts.map((c) => c.label)).toEqual(["0–10", "10–100", "100–1s", ">1s"])
  })
})
