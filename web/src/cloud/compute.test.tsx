import { QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { TooltipProvider } from "@/components/ui/tooltip"
import { makeQueryClient } from "@/lib/query-client"
import { SettingsProvider } from "@/providers/settings-provider"
import { ThemeProvider } from "@/providers/theme-provider"

import { rupeesToPaise, type Billing, type CloudInfo, type JobDetail, type Offer, type Rental, type WorkspaceRole } from "./api"
import { cloudRoutes } from "./app"
import { endReason, parseEnv, splitArgs } from "./compute"
import { CloudSessionProvider } from "./session"

const INFO: CloudInfo = {
  mode: "cloud",
  version: "0.12.0",
  providers: { google: false, email: true },
  email_delivery: "smtp",
  signup: "open",
  public_url: "https://cloud.example",
  node_url: "https://cloud.example:8443",
}
const USER = { id: "u1", email: "ada@acme.dev", name: "Ada", avatar_url: "", created_at: null }
const IMAGE = `docker.io/pytorch/pytorch@sha256:${"a".repeat(64)}`
const GB = 1024 ** 3

const OFFER: Offer = {
  id: "off_1",
  title: "2× H100, Mumbai",
  description: "Tier III, 10 GbE",
  region: "Mumbai",
  gpu_model: "NVIDIA H100",
  gpu_count: 2,
  vram_bytes_per_gpu: 80 * GB,
  price_paise_per_gpu_hour: 10000,
  fee_bps: 200,
  hourly_paise: 20000,
  hourly_fee_paise: 400,
  hourly_total_paise: 20400,
  status: "listed",
  available: true,
  unavailable_reason: "",
  provider: { name: "Mumbai DC", slug: "mumbai-dc" },
  mine: false,
  created_at: null,
}

const RENTAL: Rental = {
  id: "rnt_1",
  role: "consumer",
  status: "active",
  end_reason: "",
  offer: { id: "off_1", title: OFFER.title, region: "Mumbai", gpu_model: "NVIDIA H100", vram_bytes_per_gpu: 80 * GB },
  counterparty: "Mumbai DC",
  gpu_count: 2,
  price_paise_per_gpu_hour: 10000,
  fee_bps: 200,
  hourly_total_paise: 20400,
  amount_paise: 0,
  fee_paise: 0,
  seconds: 0,
  node_online: true,
  release_after_job_id: null,
  started_at: "2026-11-10T10:00:00Z",
  ended_at: null,
}

const billing = (available: number, extra: Partial<Billing> = {}): Billing => ({
  currency: "INR",
  fee_bps: 200,
  balance_paise: available,
  accrued_spend_paise: 0,
  accrued_earnings_paise: 0,
  available_paise: available,
  burn_paise_per_hour: 0,
  totals_paise: {},
  entries: [],
  ...extra,
})

const JOB: JobDetail = {
  id: "job_1",
  name: "finetune",
  kind: "job",
  image: IMAGE,
  status: "failed",
  message: "replica 0: exit code 137",
  replicas: 1,
  gpus_per_replica: 2,
  min_vram_bytes: 0,
  timeout_seconds: 0,
  rental_id: null,
  created_by: "ada@acme.dev",
  created_at: "2026-11-10T10:00:00Z",
  started_at: "2026-11-10T10:00:05Z",
  finished_at: "2026-11-10T10:05:05Z",
  seconds: 300,
  phases: ["Failed"],
  args: ["python", "train.py"],
  env: {},
  expose_port: false,
  placements: [
    { rank: 0, node_id: "node_1", node_name: "gpu-a", gpus: 2, phase: "Failed", exit_code: 137, reason: "NonZeroExit", message: "exit code 137", endpoint: "", started_at: null, finished_at: null, log_tail: "CUDA out of memory\n" },
  ],
}

function json(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
}

type Handler = (body: unknown) => Response
function serve(role: WorkspaceRole, routes: Record<string, Handler>, platformAdmin = false) {
  const calls: { method: string; path: string; body: unknown }[] = []
  const base: Record<string, Handler> = {
    "/api/me": () => json(200, { user: USER, workspaces: [{ id: "w1", slug: "acme", name: "Acme", role }], platform_admin: platformAdmin }),
    "/api/w/w1/nodes": () => json(200, { nodes: [] }),
    "/api/w/w1/claims": () => json(200, { claims: [] }),
    "/api/w/w1/market/offers": () => json(200, { offers: [OFFER] }),
    "/api/w/w1/market/rentals": () => json(200, { rentals: [] }),
    "/api/w/w1/billing": () => json(200, billing(1_000_000)),
    "/api/w/w1/jobs": () => json(200, { jobs: [] }),
    "/api/w/w1/api-keys": () => json(200, { keys: [] }),
  }
  const all = { ...base, ...routes }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input), "http://localhost")
      const method = (init?.method ?? "GET").toUpperCase()
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined
      calls.push({ method, path: url.pathname, body })
      const handler = all[`${method} ${url.pathname}`] ?? (method === "GET" ? all[url.pathname] : undefined)
      return handler ? handler(body) : json(404, { error: { code: "NOT_FOUND", message: "not found" } })
    }),
  )
  return calls
}

function renderAt(path: string) {
  const router = createMemoryRouter(cloudRoutes, { initialEntries: [path] })
  render(
    <ThemeProvider>
      <QueryClientProvider client={makeQueryClient({ cloud: true })}>
        <SettingsProvider>
          <TooltipProvider>
            <CloudSessionProvider info={INFO}>
              <RouterProvider router={router} />
            </CloudSessionProvider>
          </TooltipProvider>
        </SettingsProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
  return router
}

beforeEach(() => {
  window.localStorage.clear()
  const noop = () => undefined
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, onchange: null, addEventListener: noop, removeEventListener: noop, addListener: noop, removeListener: noop, dispatchEvent: () => false }))
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})
afterEach(() => vi.unstubAllGlobals())

describe("compute helpers", () => {
  it("turns typed rupees into exact paise and rejects anything else", () => {
    expect(rupeesToPaise("180")).toBe(18000)
    expect(rupeesToPaise(" 180.5 ")).toBe(18050)
    expect(rupeesToPaise("0.07")).toBe(7)
    for (const bad of ["", "abc", "1.234", "-5", "1e3", "₹100"]) expect(rupeesToPaise(bad)).toBeNull()
  })

  it("splits a command line like a shell would", () => {
    expect(splitArgs(`python train.py --name "my run" --tag 'a b' --lr=1e-4`)).toEqual(["python", "train.py", "--name", "my run", "--tag", "a b", "--lr=1e-4"])
    expect(splitArgs("   ")).toEqual([])
    expect(splitArgs(`echo "say \\"hi\\""`)).toEqual(["echo", 'say "hi"'])
  })

  it("parses NAME=value lines and names the first bad one", () => {
    expect(parseEnv("A=1\n\nURL=http://x/?a=b\n")).toEqual({ env: { A: "1", URL: "http://x/?a=b" }, bad: null })
    expect(parseEnv("A=1\nnot valid").bad).toBe("not valid")
    expect(parseEnv("MERIDIAN_JOB_RANK=3").bad).toBe("MERIDIAN_JOB_RANK=3")
  })

  it("explains why a rental ended", () => {
    expect(endReason("insufficient_funds")).toBe("Ran out of credit")
    expect(endReason("node_lost")).toBe("Node went offline")
    expect(endReason("something_new")).toBe("Ended")
  })
})

describe("Marketplace", () => {
  it("shows the price with the fee broken out and rents on confirm", async () => {
    const calls = serve("admin", { "POST /api/w/w1/market/rentals": () => json(201, RENTAL) })
    const user = userEvent.setup()
    renderAt("/marketplace")

    expect(await screen.findByText("2× H100, Mumbai")).toBeInTheDocument()
    expect(screen.getByText("₹100.00")).toBeInTheDocument()
    expect(screen.getByText("₹204.00/hour")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Rent" }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Meridian fee (2%)")).toBeInTheDocument()
    expect(within(dialog).getByText("₹4.00")).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Rent for ₹204.00/hour" }))
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/w/w1/market/rentals")).toBe(true))
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ offer_id: "off_1" })
  })

  it("blocks renting without an hour of credit and says how much to add", async () => {
    serve("admin", { "/api/w/w1/billing": () => json(200, billing(10_000)) })
    const user = userEvent.setup()
    renderAt("/marketplace")
    await user.click(await screen.findByRole("button", { name: "Rent" }))
    const dialog = await screen.findByRole("dialog")
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Add ₹104.00 or more")
    expect(within(dialog).getByRole("button", { name: /Rent for/ })).toBeDisabled()
  })

  it("lets operators browse but not rent", async () => {
    serve("operator", {})
    renderAt("/marketplace")
    expect(await screen.findByText("2× H100, Mumbai")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Rent" })).toBeDisabled()
    expect(screen.queryByRole("button", { name: /List a node/ })).not.toBeInTheDocument()
    expect(screen.getByText(/Renting needs the admin role/)).toBeInTheDocument()
  })
})

describe("Jobs", () => {
  it("submits a job with parsed command and env, renting the chosen listing", async () => {
    const calls = serve("admin", {
      "POST /api/w/w1/jobs": () => json(201, { ...JOB, status: "scheduled", message: "" }),
      "/api/w/w1/jobs/job_1": () => json(200, { ...JOB, status: "scheduled", message: "" }),
    })
    const user = userEvent.setup()
    const router = renderAt("/jobs?new=1&offer=off_1")

    const dialog = await screen.findByRole("dialog")
    const run = within(dialog).getByRole("button", { name: /Run/ })
    expect(run).toBeDisabled() // no image yet
    await user.type(within(dialog).getByLabelText("Container image"), "pytorch/pytorch:latest")
    expect(within(dialog).getByText(/Use the full name with a digest/)).toBeInTheDocument()
    await user.clear(within(dialog).getByLabelText("Container image"))
    await user.type(within(dialog).getByLabelText("Container image"), IMAGE)
    await user.type(within(dialog).getByLabelText("Command"), 'python train.py --name "run 1"')
    await user.clear(within(dialog).getByLabelText("GPUs per replica"))
    await user.type(within(dialog).getByLabelText("GPUs per replica"), "2")
    await user.type(within(dialog).getByLabelText("Environment (optional)"), "SEED=7")
    await user.click(await within(dialog).findByRole("button", { name: "Rent and run" }))

    await waitFor(() => expect(router.state.location.pathname).toBe("/jobs/job_1"))
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      kind: "job",
      image: IMAGE,
      args: ["python", "train.py", "--name", "run 1"],
      env: { SEED: "7" },
      replicas: 1,
      gpus_per_replica: 2,
      min_vram_gb: 0,
      timeout_seconds: 0,
      offer_id: "off_1",
    })
  })

  it("shows why a job failed, its logs, and offers no cancel once it is done", async () => {
    serve("operator", { "/api/w/w1/jobs/job_1": () => json(200, JOB) })
    renderAt("/jobs/job_1")
    expect(await screen.findByRole("heading", { name: "finetune" })).toBeInTheDocument()
    expect(screen.getByText("Why it failed")).toBeInTheDocument()
    expect(screen.getByText("replica 0: exit code 137")).toBeInTheDocument()
    expect(screen.getByText("CUDA out of memory")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument()
  })

  it("hides run and API-key actions from viewers", async () => {
    serve("viewer", {})
    renderAt("/jobs")
    expect(await screen.findByText("No jobs yet")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Run a job/ })).not.toBeInTheDocument()
    expect(screen.queryByText("Run from your terminal")).not.toBeInTheDocument()
  })
})

describe("Billing", () => {
  it("shows credit, burn rate and time left; platform tools only for platform admins", async () => {
    serve("admin", {
      "/api/w/w1/billing": () =>
        json(
          200,
          billing(40_800, {
            burn_paise_per_hour: 20_400,
            totals_paise: { topup: 100_000, rental_charge: -58_000, platform_fee: -1_200 },
            entries: [{ id: 1, kind: "topup", amount_paise: 100_000, rental_id: null, reference: "UTR-1", memo: "", created_at: "2026-11-10T09:00:00Z" }],
          }),
        ),
    })
    renderAt("/billing")
    expect(await screen.findByText("₹408.00")).toBeInTheDocument()
    expect(screen.getByText("About 2.0 hours of credit left")).toBeInTheDocument()
    expect(screen.getByText("₹592.00")).toBeInTheDocument() // spent, fee included
    expect(await screen.findByText("+₹1,000.00")).toBeInTheDocument()
    expect(screen.getByText("Credit added")).toBeInTheDocument()
    expect(screen.queryByText("Platform administration")).not.toBeInTheDocument()
  })

  it("lets a platform admin record a credit against a payment reference", async () => {
    const calls = serve(
      "owner",
      {
        "/api/platform/summary": () =>
          json(200, { fee_bps: 200, fee_revenue_paise: 400, gross_rental_paise: 20000, active_rentals: 1, listed_offers: 1, workspaces: [{ id: "w1", slug: "acme", name: "Acme", balance_paise: 0 }] }),
        "POST /api/platform/credits": () => json(201, { workspace_id: "w1", balance_paise: 2_500_000 }),
      },
      true,
    )
    const user = userEvent.setup()
    renderAt("/billing")
    expect(await screen.findByText("Platform administration")).toBeInTheDocument()
    const record = screen.getByRole("button", { name: "Record credit" })
    expect(record).toBeDisabled()
    await user.type(screen.getByLabelText("Amount (₹)"), "25000")
    await user.type(screen.getByLabelText("Payment reference"), "UTR-778899")
    await user.click(record)
    await waitFor(() => expect(calls.some((c) => c.path === "/api/platform/credits")).toBe(true))
    expect(calls.find((c) => c.path === "/api/platform/credits")?.body).toEqual({ workspace: "w1", amount_paise: 2_500_000, reference: "UTR-778899", memo: "" })
  })
})
