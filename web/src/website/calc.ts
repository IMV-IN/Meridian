// The two calculators on the public site. All arithmetic is in integer paise,
// with the same rounding as meridian_control.compute (see lib/format.ts).
import { feeFor, formatINR, rentalDue } from "@/lib/format"

/** The marketplace fee when the server does not say otherwise (basis points). */
export const FEE_BPS = 200

/** "1,200" or " 12.5 " → 1200 / 12.5. Anything that is not a plain number is NaN. */
export function parseNumber(text: string): number {
  const t = text.trim().replace(/,/g, "")
  return /^(\d+\.?\d*|\.\d+)$/.test(t) ? Number(t) : NaN
}

export const MAX_GPUS = 64
export const MAX_HOURS = 744 // a 31-day month
export const MAX_RATE_RUPEES = 100_000

export type Field = "gpus" | "hours" | "rate"
export type Problem = { field: Field; message: string }

export interface RentalQuote {
  line: string
  subtotal: number
  fee: number
  total: number
}

const gpusOk = (g: number) => Number.isInteger(g) && g >= 1 && g <= MAX_GPUS
const GPU_MSG = `GPUs must be a whole number from 1 to ${MAX_GPUS}.`

/** What renting costs: price × GPUs × time, plus the fee. The provider receives `subtotal`. */
export function rentalQuote(gpusText: string, hoursText: string, rateText: string, bps = FEE_BPS): RentalQuote | Problem {
  const g = parseNumber(gpusText)
  const h = parseNumber(hoursText)
  const r = parseNumber(rateText)
  if (!gpusOk(g)) return { field: "gpus", message: GPU_MSG }
  if (!(h > 0 && h <= MAX_HOURS)) return { field: "hours", message: `Hours must be more than 0 and at most ${MAX_HOURS} (a month).` }
  if (!(r >= 0 && r <= MAX_RATE_RUPEES)) return { field: "rate", message: "Price must be between ₹0 and ₹1,00,000 per GPU-hour." }
  const price = Math.round(r * 100)
  const subtotal = rentalDue(price, g, Math.round(h * 3600))
  const fee = feeFor(subtotal, bps)
  return { line: `${g} GPU${g === 1 ? "" : "s"} × ${h} h × ${formatINR(price)}`, subtotal, fee, total: subtotal + fee }
}

export interface Earnings {
  day: number
  renterDay: number
  month: number
}

/** What listing idle GPUs could earn: the owner keeps the full price; renters pay it plus the fee. */
export function earningsEstimate(gpusText: string, rateText: string, hoursPerDay: number, bps = FEE_BPS): Earnings | Problem {
  const g = parseNumber(gpusText)
  const r = parseNumber(rateText)
  if (!gpusOk(g)) return { field: "gpus", message: GPU_MSG }
  if (!(r >= 1 && r <= MAX_RATE_RUPEES)) return { field: "rate", message: "A public listing costs at least ₹1 per GPU-hour." }
  const day = Math.round(r * 100) * g * hoursPerDay
  return { day, renterDay: day + feeFor(day, bps), month: day * 30 }
}

export const isProblem = (x: object): x is Problem => "message" in x

/** Whole rupees with Indian grouping, no paise: 14400000 → "₹1,44,000". */
export const formatWholeINR = (paise: number) => `₹${Math.floor(paise / 100).toLocaleString("en-IN")}`
