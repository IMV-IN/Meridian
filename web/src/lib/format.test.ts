import { describe, expect, it } from "vitest"

import {
  backendLabel,
  formatBytes,
  formatINR,
  formatVram,
  formatEwma,
  formatMs,
  formatMsShort,
  formatNumber,
  formatPercent,
  formatSeconds,
  percentile,
  statusClass,
} from "./format"
import { capabilitiesFor } from "./rbac"

describe("formatters", () => {
  it("formats numbers with auto-compaction at 10k", () => {
    expect(formatNumber(1284)).toBe("1,284")
    expect(formatNumber(12_900)).toBe("12.9K")
    expect(formatNumber(null)).toBe("—")
    expect(formatNumber(0.1234, { digits: 2 })).toBe("0.12")
  })

  it("formats latency across magnitudes", () => {
    expect(formatMs(0.5)).toBe("0.50 ms")
    expect(formatMs(4.25)).toBe("4.3 ms")
    expect(formatMs(42)).toBe("42 ms")
    expect(formatMs(1240)).toBe("1.24 s")
    expect(formatMs(120_000)).toBe("2.0 min")
    expect(formatMsShort(750)).toBe("750ms")
    expect(formatMsShort(1500)).toBe("1.5s")
    expect(formatEwma(0)).toBe("—")
  })

  it("formats durations, percents and bytes", () => {
    expect(formatSeconds(45)).toBe("45s")
    expect(formatSeconds(300)).toBe("5m")
    expect(formatSeconds(7800)).toBe("2h 10m")
    expect(formatPercent(0.0004)).toBe("<0.1%")
    expect(formatPercent(1)).toBe("100%")
    expect(formatBytes(8 * 2 ** 30, 0)).toBe("8 GiB")
    expect(formatBytes(512)).toBe("512 B")
  })

  it("classifies status codes and computes nearest-rank percentiles", () => {
    expect(statusClass(204)).toBe("2xx")
    expect(statusClass(429)).toBe("4xx")
    expect(statusClass(503)).toBe("5xx")
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95)).toBe(10)
    expect(percentile([1, 2, 3, 4], 50)).toBe(2)
    expect(percentile([], 50)).toBeNull()
  })

  it("splits managed backend names into engine + node", () => {
    expect(backendLabel("managed:node_abc:eng-1")).toEqual({ primary: "eng-1", secondary: "node node_abc", managed: true })
    expect(backendLabel("gpu-a")).toEqual({ primary: "gpu-a", secondary: null, managed: false })
  })
})

describe("capabilitiesFor mirrors IdentityContext.can_*", () => {
  it.each([
    [{ role: null, cost_admin: false, ops_admin: false }, { view_ops: false, reload: false, manage_keys: false, read_all_cost: false }],
    [{ role: "viewer", cost_admin: false, ops_admin: false }, { view_ops: true, reload: false, manage_keys: false, read_all_cost: false }],
    [{ role: "operator", cost_admin: false, ops_admin: false }, { view_ops: true, reload: true, manage_keys: false, read_all_cost: false }],
    [{ role: "admin", cost_admin: false, ops_admin: false }, { view_ops: true, reload: true, manage_keys: true, read_all_cost: true }],
    [{ role: null, cost_admin: true, ops_admin: false }, { view_ops: true, reload: false, manage_keys: false, read_all_cost: true }],
    [{ role: null, cost_admin: false, ops_admin: true }, { view_ops: true, reload: true, manage_keys: true, read_all_cost: false }],
  ] as const)("%o", (k, expected) => {
    expect(capabilitiesFor(k)).toEqual(expected)
  })
})

describe("formatINR", () => {
  it("formats integer paise as rupees with Indian grouping", () => {
    expect(formatINR(20400)).toBe("₹204.00")
    expect(formatINR(5)).toBe("₹0.05")
    expect(formatINR(1234567805)).toBe("₹1,23,45,678.05")
    expect(formatINR(0)).toBe("₹0.00")
  })
  it("marks debits and, when asked, credits", () => {
    expect(formatINR(-10200)).toBe("−₹102.00")
    expect(formatINR(10200, { signed: true })).toBe("+₹102.00")
    expect(formatINR(null)).toBe("—")
  })
  it("formats VRAM in GB", () => {
    expect(formatVram(80 * 1024 ** 3)).toBe("80 GB")
    expect(formatVram(0)).toBe("—")
  })
})
