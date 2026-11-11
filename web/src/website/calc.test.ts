import { describe, expect, it } from "vitest"

import { formatINR } from "@/lib/format"

import { earningsEstimate, formatWholeINR, isProblem, parseNumber, rentalQuote } from "./calc"

describe("rental calculator", () => {
  it("prices 2 GPUs for half an hour at ₹120 as ₹120.00 plus a ₹2.40 fee", () => {
    const q = rentalQuote("2", "0.5", "120")
    if (isProblem(q)) throw new Error(q.message)
    expect(formatINR(q.subtotal)).toBe("₹120.00")
    expect(formatINR(q.fee)).toBe("₹2.40")
    expect(formatINR(q.total)).toBe("₹122.40")
    expect(q.line).toBe("2 GPUs × 0.5 h × ₹120.00")
  })

  it("matches the second worked example", () => {
    const q = rentalQuote("4", "2", "185")
    if (isProblem(q)) throw new Error(q.message)
    expect([formatINR(q.subtotal), formatINR(q.fee), formatINR(q.total)]).toEqual(["₹1,480.00", "₹29.60", "₹1,509.60"])
  })

  it("accepts commas and a single GPU", () => {
    const q = rentalQuote("1", "1", "1,200")
    if (isProblem(q)) throw new Error(q.message)
    expect(q.line).toBe("1 GPU × 1 h × ₹1,200.00")
    expect(formatINR(q.total)).toBe("₹1,224.00")
  })

  it("is free at ₹0 and names the field that is wrong otherwise", () => {
    const free = rentalQuote("2", "1", "0")
    if (isProblem(free)) throw new Error(free.message)
    expect(free.total).toBe(0)
    expect(rentalQuote("1.5", "1", "10")).toEqual({ field: "gpus", message: "GPUs must be a whole number from 1 to 64." })
    expect(rentalQuote("65", "1", "10")).toMatchObject({ field: "gpus" })
    expect(rentalQuote("2", "0", "10")).toEqual({ field: "hours", message: "Hours must be more than 0 and at most 744 (a month)." })
    expect(rentalQuote("2", "745", "10")).toMatchObject({ field: "hours" })
    expect(rentalQuote("2", "1", "100001")).toEqual({ field: "rate", message: "Price must be between ₹0 and ₹1,00,000 per GPU-hour." })
    expect(rentalQuote("", "1", "10")).toMatchObject({ field: "gpus" })
  })

  it("parses plain numbers only", () => {
    expect(parseNumber(" 1,234.5 ")).toBe(1234.5)
    expect(parseNumber(".5")).toBe(0.5)
    expect(parseNumber("12abc")).toBeNaN()
    expect(parseNumber("")).toBeNaN()
    expect(parseNumber("-3")).toBeNaN()
  })
})

describe("earnings estimator", () => {
  it("estimates 4 GPUs at ₹150 for 8 hours a day", () => {
    const e = earningsEstimate("4", "150", 8)
    if (isProblem(e)) throw new Error(e.message)
    expect(formatINR(e.day)).toBe("₹4,800.00")
    expect(formatINR(e.renterDay)).toBe("₹4,896.00")
    expect(formatWholeINR(e.month)).toBe("₹1,44,000")
  })

  it("earns nothing when nobody rents it", () => {
    const e = earningsEstimate("4", "150", 0)
    if (isProblem(e)) throw new Error(e.message)
    expect(e.month).toBe(0)
  })

  it("refuses a public price under ₹1 and a bad GPU count", () => {
    expect(earningsEstimate("4", "0.5", 8)).toEqual({ field: "rate", message: "A public listing costs at least ₹1 per GPU-hour." })
    expect(earningsEstimate("0", "150", 8)).toMatchObject({ field: "gpus" })
  })
})
