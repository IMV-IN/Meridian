// What a rental costs, and what idle GPUs could earn. The arithmetic is in calc.ts.
import { useState, type ReactNode } from "react"

import { formatINR } from "@/lib/format"

import { earningsEstimate, FEE_BPS, formatWholeINR, isProblem, parseNumber, rentalQuote, type Field, type Problem } from "./calc"

function Stepper({ id, label, noun, value, onChange, step, min, invalid, describedBy, inputMode = "numeric" }: { id: string; label: string; noun: string; value: string; onChange: (v: string) => void; step: number; min: number; invalid: boolean; describedBy: string; inputMode?: "numeric" | "decimal" }) {
  const bump = (d: number) => onChange(String(+Math.max(min, (parseNumber(value) || 0) + d).toFixed(2)))
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="stepper">
        <button type="button" onClick={() => bump(-step)} aria-label={`Fewer ${noun}`}>
          −
        </button>
        <input id={id} inputMode={inputMode} value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={invalid} aria-describedby={describedBy} />
        <button type="button" onClick={() => bump(step)} aria-label={`More ${noun}`}>
          +
        </button>
      </div>
    </div>
  )
}

function RupeeInput({ id, label, value, onChange, invalid, describedBy }: { id: string; label: string; value: string; onChange: (v: string) => void; invalid: boolean; describedBy: string }) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="prefix">
        <span aria-hidden="true">₹</span>
        <input id={id} inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={invalid} aria-describedby={describedBy} />
      </div>
    </div>
  )
}

const Ledger = ({ children }: { children: ReactNode }) => (
  <div className="ledger" aria-live="polite">
    {children}
  </div>
)

/** Inputs as typed, plus the last set that was valid: the ledger keeps showing that while a field is wrong. */
function useChecked<I extends Record<string, string | number>>(initial: I, check: (i: I) => Problem | null) {
  const [inputs, setInputs] = useState(initial)
  const [valid, setValid] = useState(initial)
  const set = (patch: Partial<I>) => {
    const next = { ...inputs, ...patch }
    setInputs(next)
    if (!check(next)) setValid(next)
  }
  return { inputs, valid, set, problem: check(inputs) }
}
const problemOf = (r: object): Problem | null => (isProblem(r) ? r : null)

export interface RentalPreset {
  gpus: number
  ratePaise: number
  nonce: number
}

export function RentalCalculator({ preset, feeBps = FEE_BPS }: { preset?: RentalPreset; feeBps?: number }) {
  // `preset` is only the starting point: MarketSection remounts this with a new key when "Price it" is used.
  const { inputs, valid, set, problem } = useChecked(
    { gpus: String(preset?.gpus ?? 2), hours: "0.5", rate: String(preset ? preset.ratePaise / 100 : 120) },
    (i) => problemOf(rentalQuote(i.gpus, i.hours, i.rate, feeBps)),
  )
  const last = rentalQuote(valid.gpus, valid.hours, valid.rate, feeBps)
  const quote = isProblem(last) ? null : last
  const bad = (f: Field) => problem?.field === f
  return (
    <section className="calc" aria-labelledby="calc-title" id="calc">
      <h3 id="calc-title">What a rental costs</h3>
      <p>Billed per second, from the moment you rent until you release.</p>
      <div className="calc-inputs">
        <Stepper id="r-gpus" label="GPUs" noun="GPUs" value={inputs.gpus} onChange={(gpus) => set({ gpus })} step={1} min={1} invalid={bad("gpus")} describedBy="r-err" />
        <Stepper id="r-hours" label="Hours" noun="hours" value={inputs.hours} onChange={(hours) => set({ hours })} step={0.5} min={0.5} invalid={bad("hours")} describedBy="r-err" inputMode="decimal" />
        <RupeeInput id="r-rate" label="Price per GPU-hour" value={inputs.rate} onChange={(rate) => set({ rate })} invalid={bad("rate")} describedBy="r-err" />
      </div>
      <p className="err-text" id="r-err" role="alert">
        {problem?.message}
      </p>
      {quote && (
        <>
          <Ledger>
            <div>
              <span>{quote.line}</span>
              <b className="num">{formatINR(quote.subtotal)}</b>
            </div>
            <div>
              <span>Meridian fee, {feeBps / 100}%</span>
              <b className="num">{formatINR(quote.fee)}</b>
            </div>
            <div className="total">
              <span>You pay</span>
              <b className="num">{formatINR(quote.total)}</b>
            </div>
          </Ledger>
          <p className="fine">The provider receives {formatINR(quote.subtotal)}. On your own nodes the same job costs nothing. A rental keeps the price it started at.</p>
        </>
      )}
    </section>
  )
}

export function EarningsEstimator({ feeBps = FEE_BPS }: { feeBps?: number }) {
  const { inputs, valid, set, problem } = useChecked(
    { gpus: "4", rate: "150", hours: 8 },
    (i) => problemOf(earningsEstimate(i.gpus, i.rate, i.hours, feeBps)),
  )
  const last = earningsEstimate(valid.gpus, valid.rate, valid.hours, feeBps)
  const est = isProblem(last) ? null : last
  const hours = inputs.hours
  const bad = (f: Field) => problem?.field === f
  return (
    <section className="calc" aria-labelledby="earn-title" id="earn">
      <h3 id="earn-title">What your idle GPUs could earn</h3>
      <p>You set the price. Renters pay it plus the {feeBps / 100}% fee, and you receive the full price.</p>
      <div className="calc-inputs">
        <Stepper id="e-gpus" label="GPUs listed" noun="GPUs" value={inputs.gpus} onChange={(gpus) => set({ gpus })} step={1} min={1} invalid={bad("gpus")} describedBy="e-err" />
        <RupeeInput id="e-rate" label="Your price per GPU-hour" value={inputs.rate} onChange={(rate) => set({ rate })} invalid={bad("rate")} describedBy="e-err" />
        <div>
          <label htmlFor="e-hours">
            Hours rented a day: <span className="num">{hours}</span>
          </label>
          <input className="range" type="range" id="e-hours" min={0} max={24} step={1} value={hours} onChange={(e) => set({ hours: Number(e.target.value) })} />
        </div>
      </div>
      <p className="err-text" id="e-err" role="alert">
        {problem?.message}
      </p>
      {est && (
        <>
          <Ledger>
            <div>
              <span>A day</span>
              <b className="num">{formatINR(est.day)}</b>
            </div>
            <div>
              <span>Renters pay a day, with the fee</span>
              <b className="num">{formatINR(est.renterDay)}</b>
            </div>
            <div className="total">
              <span>You receive in 30 days</span>
              <b className="num">{formatWholeINR(est.month)}</b>
            </div>
          </Ledger>
          <p className="fine">An estimate, if a renter holds the listing for that many hours each day. Earnings are paid out to your bank account on request; there is no card or UPI checkout.</p>
        </>
      )}
    </section>
  )
}
