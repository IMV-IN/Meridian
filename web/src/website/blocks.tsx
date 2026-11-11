// Blocks shared by the landing page and the audience pages.
import { useState } from "react"
import { Link } from "react-router"

import { EarningsEstimator, RentalCalculator, type RentalPreset } from "./calculators"
import { LiveListings } from "./live-listings"
import type { PublicOffer } from "./offers"
import { CodeBlock } from "./ui"
import { COMPUTE_GUIDE, installCommand } from "./site-lib"

/** Listings with the two calculators. "Price it" on a listing fills the rental calculator. */
export function MarketSection() {
  const [preset, setPreset] = useState<RentalPreset>()
  const price = (o: PublicOffer) => {
    setPreset({ gpus: o.gpu_count, ratePaise: o.price_paise_per_gpu_hour, nonce: Date.now() })
    window.setTimeout(() => {
      const calc = document.getElementById("calc")
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      calc?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" })
      document.getElementById("r-hours")?.focus({ preventScroll: true })
    }, 0)
  }
  return (
    <>
      <LiveListings onPrice={price} />
      <div className="calcs">
        <RentalCalculator key={preset?.nonce ?? 0} preset={preset} />
        <EarningsEstimator />
      </div>
    </>
  )
}

/** "What it does not do yet". Keep in step with the same heading in docs/COMPUTE.md. */
const LIMITS: Record<string, [string, string]> = {
  storage: ["No storage service", "There are no managed volumes or datasets. A job brings its own data and saves its own results."],
  payments: ["No card or UPI payments", "Credit is added by bank transfer and recorded against your payment reference. Payouts are made the same way."],
  restarts: ["No restarts", "A failed job or model server is not restarted. Checkpoint, and submit again."],
  secrets: ["No secrets store", "Job environment variables are stored as plain text. Do not put tokens in them."],
  containers: ["Containers, not virtual machines", "Jobs run in hardened Docker containers. Only list hosts you are prepared to treat that way."],
  network: ["No networking between institutes", "A partner's job reaches only what the host's network allows. Meridian does not tunnel between campuses."],
  fcfs: ["First come, first served", "There are no job priorities. Quotas are per person and per month, not per group."],
  replica: ["One control-plane replica", "Compute scheduling runs on a single control-plane process."],
}

export function Limits({ only, titleId = "lim-title", lead = "Read this before you plan a semester around it." }: { only?: string[]; titleId?: string; lead?: string }) {
  const keys = only ?? Object.keys(LIMITS)
  return (
    <section className="section" aria-labelledby={titleId}>
      <div className="wrap">
        <h2 className="h2" id={titleId}>
          What it does not do yet
        </h2>
        <p className="lead mt-4">{lead}</p>
        <div className="limits">
          {keys.map((k) => (
            <div key={k}>
              <b>{LIMITS[k][0]}</b>
              <p>{LIMITS[k][1]}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export function FinalCta() {
  const code = `[[p:$]] ${installCommand(window.location.origin)}   [[c:# puts the meridian command in ~/.meridian/venv/bin]]
[[p:$]] export MERIDIAN_CLOUD_URL=https://<your-meridian-cloud>
[[p:$]] export MERIDIAN_WORKSPACE=ws_…
[[p:$]] export MERIDIAN_API_KEY=mck_…   [[c:# Settings, API keys]]
[[p:$]] meridian compute run --template python-script --follow`
  return (
    <section className="final" aria-labelledby="final-title">
      <div className="wrap final-grid">
        <div>
          <h2 className="display" id="final-title">
            Start with one server.
          </h2>
          <p className="lead mt-5">Enroll a GPU host, run the Python hello-world template, and you will see the GPU's name in the job's output. Add the rest of the fleet when it works.</p>
          <div className="hero-actions">
            <a className="btn btn-lamp" href={COMPUTE_GUIDE}>
              Read the compute guide
            </a>
            <a className="btn btn-ghost" href="/ui/">
              Sign in to the console
            </a>
          </div>
        </div>
        <CodeBlock head="after enrolling a node" code={code} />
      </div>
    </section>
  )
}

/** A link to an audience page, for the routes strip and the audiences band. */
export const PageLink = ({ to, children }: { to: string; children: string }) => (
  <Link className="link" to={to}>
    {children}
  </Link>
)

export const DDP_RUN = `[[p:$]] meridian compute run --name resnet-ddp \\
    --replicas 2 --gpus 4 --follow \\
    --image docker.io/pytorch/pytorch@sha256:77f17f84… -- \\
  torchrun --nnodes={{replicas}} --nproc-per-node=4 \\
    --rdzv-backend=c10d --rdzv-endpoint={{master}} \\
    --rdzv-id={{job_id}} train.py

[[d:submitted job_3b90e1c4a7f25d6e8c01 (queued)]]
[[d:scheduled: Pending, Pending]]
[[d:scheduled: Starting, Starting]]
[[ok:running: Running, Running]]`

/** The variables each replica of a multi-node job gets. */
export function ReplicaEnv() {
  return (
    <div>
      <p className="small body-2" style={{ marginBottom: 12 }}>
        Each replica gets these, as environment variables and as <code>{"{{…}}"}</code> placeholders in the command:
      </p>
      <div className="env-wrap">
        <table className="env">
          <thead>
            <tr>
              <th scope="col">Variable</th>
              <th scope="col">Value</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <code>MERIDIAN_JOB_RANK</code>
                <br />
                <code>MERIDIAN_JOB_REPLICAS</code>
              </td>
              <td>This replica's index and the total</td>
            </tr>
            <tr>
              <td>
                <code>MERIDIAN_JOB_PORT</code>
              </td>
              <td>The port published for this replica</td>
            </tr>
            <tr>
              <td>
                <code>MERIDIAN_JOB_PEERS</code>
              </td>
              <td>
                <code>host:port</code> of every replica, in rank order
              </td>
            </tr>
            <tr>
              <td>
                <code>MERIDIAN_JOB_MASTER</code>
              </td>
              <td>
                <code>host:port</code> of rank 0
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="small body-2" style={{ marginTop: 16 }}>
        Check the network first with the <code>pytorch-ddp</code> template. Each replica prints <code>all_reduce sum=3 (expected 3)</code> when the two nodes can reach each other. Write checkpoints to your own storage from inside the job; the console keeps the last 8 KB of each replica's output.
      </p>
    </div>
  )
}
