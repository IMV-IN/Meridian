import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { LiveListings } from "./live-listings"
import { arrange, type PublicOffer, type PublicOffers } from "./offers"

const GB = 1024 ** 3

// The real feed also sends provider_name; the site must never show it.
const offer = (o: Partial<PublicOffer> & { provider_name?: string }): PublicOffer & { provider_name: string } => ({
  title: "4× A100, Mumbai",
  region: "Mumbai",
  gpu_model: "A100",
  gpu_count: 4,
  vram_bytes_per_gpu: 80 * GB,
  price_paise_per_gpu_hour: 18500,
  hourly_total_paise: 75480,
  available: true,
  provider_name: "Secret Provider Pvt Ltd",
  ...o,
})

const feed = (offers: ReturnType<typeof offer>[]): PublicOffers & { offers: ReturnType<typeof offer>[] } => ({
  offers,
  total_offers: offers.length,
  summary: { available_gpus: 0, gpus_by_model: {}, regions: [], lowest_price_paise_per_gpu_hour: null },
})

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn<() => Promise<Response>>(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

function renderListings(onPrice = vi.fn<(o: PublicOffer) => void>()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LiveListings onPrice={onPrice} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { onPrice, client }
}

afterEach(() => vi.unstubAllGlobals())

describe("LiveListings", () => {
  it("shows a loading state until the feed answers", async () => {
    vi.stubGlobal("fetch", vi.fn<() => Promise<Response>>(() => new Promise(() => {})))
    renderListings()
    expect(screen.getByText("Loading listings", { selector: ".sr-only" })).toBeInTheDocument()
    expect(document.querySelector(".offers")).toHaveAttribute("aria-busy", "true")
  })

  it("lists offers with the region and paise prices in rupees, and never the provider", async () => {
    respond(feed([offer({}), offer({ title: "2× L40S, Pune", region: "Pune", gpu_model: "L40S", gpu_count: 2, price_paise_per_gpu_hour: 9200, hourly_total_paise: 18768 })]))
    renderListings()
    const table = await screen.findByRole("table", { name: "GPU listings" })
    expect(within(table).getByText("4 × A100")).toBeInTheDocument()
    expect(within(table).getByText("Mumbai")).toBeInTheDocument()
    expect(within(table).getByText("₹185.00")).toBeInTheDocument()
    expect(within(table).getByText("₹754.80")).toBeInTheDocument()
    expect(screen.getByText("2 of 2 listings available. Updated every 30 seconds.")).toBeInTheDocument()
    expect(document.body.textContent).not.toContain("Secret Provider")
  })

  it("puts available listings first and marks a rented one", async () => {
    respond(feed([offer({ title: "cheap but rented", gpu_model: "T4", price_paise_per_gpu_hour: 100, hourly_total_paise: 408, available: false }), offer({})]))
    renderListings()
    const rows = within(await screen.findByRole("table")).getAllByRole("row")
    expect(rows[1]).toHaveTextContent("4 × A100")
    expect(rows[2]).toHaveTextContent("rented now")
    expect(rows[2]).toHaveTextContent("Rented")
  })

  it("passes the listing to the calculator when Price it is used", async () => {
    respond(feed([offer({})]))
    const { onPrice } = renderListings()
    await userEvent.click(await screen.findByRole("button", { name: "Price 4 × A100 in the calculator" }))
    expect(onPrice).toHaveBeenCalledWith(expect.objectContaining({ gpu_count: 4, price_paise_per_gpu_hour: 18500 }))
  })

  it("explains an empty marketplace and points at the console", async () => {
    respond(feed([]))
    renderListings()
    expect(await screen.findByRole("heading", { name: "No GPUs are listed right now" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "List a node" })).toHaveAttribute("href", "/ui/marketplace?tab=listings")
    expect(screen.getByText("No listings right now.")).toBeInTheDocument()
  })

  it("says so when the feed fails, and retries on request", async () => {
    const fetchMock = respond({ detail: "down" }, 503)
    renderListings()
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Listings could not be loaded")
    expect(alert).toHaveTextContent("HTTP 503")
    expect(screen.getByText("Listings unavailable")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Open the marketplace in the console" })).toHaveAttribute("href", "/ui/marketplace")

    respond(feed([offer({})]))
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("table")).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("treats a body that is not a feed as a failure", async () => {
    respond({ nope: true })
    renderListings()
    expect(await screen.findByRole("alert")).toHaveTextContent("Listings could not be loaded")
  })

  it("keeps the last listings on screen when a refresh fails", async () => {
    respond(feed([offer({})]))
    const { client } = renderListings()
    await screen.findByRole("table")
    respond({}, 500)
    await client.refetchQueries({ queryKey: ["public-offers"] })
    await waitFor(() => expect(screen.getByText(/The feed did not answer; retrying\./)).toBeInTheDocument())
    expect(screen.getByRole("table")).toBeInTheDocument()
  })
})

describe("arrange", () => {
  const a = offer({ title: "a", gpu_model: "A100", gpu_count: 2, price_paise_per_gpu_hour: 300 })
  const b = offer({ title: "b", gpu_model: "L40S", gpu_count: 8, price_paise_per_gpu_hour: 100 })
  const c = offer({ title: "c", gpu_model: "A100", gpu_count: 4, price_paise_per_gpu_hour: 50, available: false })

  it("sorts by price with rented listings last, without changing the input", () => {
    const input = [c, a, b]
    expect(arrange(input, "", "price").map((o) => o.title)).toEqual(["b", "a", "c"])
    expect(input.map((o) => o.title)).toEqual(["c", "a", "b"])
  })

  it("filters by GPU model and sorts by GPU count", () => {
    expect(arrange([a, b, c], "A100", "gpus").map((o) => o.title)).toEqual(["a", "c"])
    expect(arrange([a, b, c], "", "gpus").map((o) => o.title)).toEqual(["b", "a", "c"])
  })
})
