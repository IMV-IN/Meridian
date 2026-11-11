// Block 4: listing idle GPUs publicly, or only for named partner workspaces on the same Meridian Cloud.
// The dialog is the console's "List a node's GPUs" (web/src/cloud/pages/marketplace.tsx), filled with sample values.
import { useState, type ReactNode } from "react"

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

import { ConsoleFrame } from "./ui"

type Mode = "public" | "private"

const COPY: Record<Mode, [string, ReactNode][]> = {
  public: [
    ["You set the price", "A price per GPU-hour, at least ₹1. Renters pay it plus a 2% Meridian fee, per second, and you receive the full price."],
    ["Anyone on the marketplace can rent it", "A listing is rented whole, by one renter at a time. You can end a rental for maintenance; you are paid for the time used."],
  ],
  private: [
    ["Name the partner workspaces", "Only the workspaces you list can see or rent it. To everyone else the listing does not exist. Partners are workspaces on the same Meridian Cloud as yours."],
    ["The price can be ₹0", "Share GPUs with a partner institute at no charge, and a free rental needs no credit. A free listing must be private: a public one costs at least ₹1."],
    ["Taking a partner off the list ends its free rental", "Its jobs on the node stop, and it can no longer rent the listing. Adding a partner ends nothing."],
  ],
}

const Field = ({ label, children, help }: { label: string; children: ReactNode; help?: ReactNode }) => (
  <div>
    <label>{label}</label>
    <div className="f">{children}</div>
    {help && <p className="help">{help}</p>}
  </div>
)

function ListNodeDialog({ mode }: { mode: Mode }) {
  const priv = mode === "private"
  return (
    <ConsoleFrame label={`List a node's GPUs dialog, ${priv ? "partners only" : "public listing"}, sample values`}>
      <div className="c-body">
        <div className="c-h1" style={{ fontSize: 15 }}>
          List a node's GPUs
        </div>
        <p className="c-desc" style={{ fontSize: 12 }}>
          Renters get the node's GPUs exclusively and run their own containers on them. You set the price and can pause the listing at any time.
        </p>
        <div className="form-mock" style={{ marginTop: 14 }}>
          <Field label="Node">lab-ws-07 · 2× RTX A6000 48 GB</Field>
          <Field label="Title">2× RTX A6000, Bengaluru</Field>
          <Field label="Who can rent it">{priv ? "Private: only workspaces you name" : "Public: anyone on the marketplace"}</Field>
          {priv && (
            <Field label="Share with workspaces" help="Workspace slugs (shown on their Settings page), separated by commas. Only they can see or rent this listing, and you can price it at ₹0 to share it free.">
              chem-dept-sample, bio-lab-sample
            </Field>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Location">Bengaluru</Field>
            <Field label="Price per GPU-hour (₹)">{priv ? "0" : "150"}</Field>
          </div>
          <p className="help">
            {priv ? "Shared free: partners pay nothing and you earn nothing." : "You earn ₹300.00 per hour while it is rented. The renter pays that plus Meridian's 2% fee."}
          </p>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
          <span className="c-btn out">Cancel</span>
          <span className="c-btn">List on marketplace</span>
        </div>
      </div>
    </ConsoleFrame>
  )
}

export function ShareMode() {
  const [mode, setMode] = useState<Mode>("public")
  return (
    <>
      <ToggleGroup type="single" value={mode} onValueChange={(v) => v && setMode(v as Mode)} aria-label="How to share" className="mode" spacing={0}>
        <ToggleGroupItem value="public" className="mode-btn">
          Public listing
        </ToggleGroupItem>
        <ToggleGroupItem value="private" className="mode-btn">
          Partners only
        </ToggleGroupItem>
      </ToggleGroup>
      <div className="mode-body">
        <dl className="deflist" aria-live="polite">
          {COPY[mode].map(([term, text]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{text}</dd>
            </div>
          ))}
        </dl>
        <ListNodeDialog mode={mode} />
      </div>
    </>
  )
}
