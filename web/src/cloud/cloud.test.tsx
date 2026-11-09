import { QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { TooltipProvider } from "@/components/ui/tooltip"
import { makeQueryClient } from "@/lib/query-client"
import { SettingsProvider } from "@/providers/settings-provider"
import { ThemeProvider } from "@/providers/theme-provider"

import { atLeast, detectCloud, readBootInfo, type CloudInfo, type Me } from "./api"
import { cloudRoutes } from "./app"
import { CloudSessionProvider } from "./session"

const INFO: CloudInfo = {
  mode: "cloud",
  version: "0.12.0",
  providers: { google: true, email: true },
  email_delivery: "smtp",
  signup: "open",
  public_url: "https://meridian.example.ts.net",
  node_url: "https://meridian.example.ts.net:8443",
}

const USER = { id: "u1", email: "ada@acme.dev", name: "Ada Lovelace", avatar_url: "", created_at: "2026-11-01T00:00:00Z" }

function json(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
}

type Route = (body: unknown, method: string) => Response
/** A tiny in-memory Meridian Cloud: path → handler, with a call log. */
function fakeCloud(routes: Record<string, Route>) {
  const calls: { method: string; path: string; headers: Record<string, string>; body: unknown }[] = []
  const impl = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input), "http://localhost")
    const method = (init?.method ?? "GET").toUpperCase()
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined
    calls.push({ method, path: url.pathname, headers: (init?.headers ?? {}) as Record<string, string>, body })
    const handler = routes[`${method} ${url.pathname}`] ?? routes[url.pathname]
    return handler ? handler(body, method) : json(404, { detail: { code: "not_found", message: "not found" } })
  })
  return { impl, calls }
}

function renderCloud(path = "/") {
  const client = makeQueryClient({ cloud: true })
  const router = createMemoryRouter(cloudRoutes, { initialEntries: [path] })
  render(
    <ThemeProvider>
    <QueryClientProvider client={client}>
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
  return { client, router }
}

beforeEach(() => {
  window.localStorage.clear()
  const noop = () => undefined
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, onchange: null, addEventListener: noop, removeEventListener: noop, addListener: noop, removeListener: noop, dispatchEvent: () => false }))
  // input-otp positions its caret with elementFromPoint, which jsdom lacks.
  document.elementFromPoint = () => null
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe("detectCloud()", () => {
  const res = (status: number, body: string, type = "application/json") => new Response(body, { status, headers: { "content-type": type } })

  it("recognises Meridian Cloud and falls back to the gateway console otherwise", async () => {
    expect(await detectCloud(async () => res(200, JSON.stringify(INFO)), [])).toEqual(INFO)
    // A self-hosted gateway: fast 404, or an HTML SPA fallback.
    expect(await detectCloud(async () => res(404, "{}"), [])).toBeNull()
    expect(await detectCloud(async () => res(200, "<!doctype html>", "text/html"), [])).toBeNull()
    expect(await detectCloud(async () => res(200, JSON.stringify({ mode: "gateway" })), [])).toBeNull()
  })

  it("retries a restarting control plane before giving up", async () => {
    const f = vi.fn<typeof fetch>().mockResolvedValueOnce(res(502, "")).mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(res(200, JSON.stringify(INFO)))
    expect(await detectCloud(f, [0, 0])).toEqual(INFO)
    expect(f).toHaveBeenCalledTimes(3)
    const down = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline"))
    expect(await detectCloud(down, [0, 0])).toBeNull()
    expect(down).toHaveBeenCalledTimes(3)
  })
})

describe("readBootInfo()", () => {
  const doc = (html: string) => new DOMParser().parseFromString(`<!doctype html><html><head>${html}</head><body></body></html>`, "text/html")

  it("reads the boot info meridian-control embeds, and knows when there is none", () => {
    expect(readBootInfo(doc(`<script id="meridian-boot" type="application/json">${JSON.stringify(INFO)}</script>`))).toEqual(INFO)
    // A self-hosted gateway serves the page without the block: gateway mode, no probe.
    expect(readBootInfo(doc(""))).toBeUndefined()
    expect(readBootInfo(doc(`<script id="meridian-boot" type="application/json">{"mode":"gateway"}</script>`))).toBeNull()
    expect(readBootInfo(doc(`<script id="meridian-boot" type="application/json">not json</script>`))).toBeNull()
  })
})

describe("atLeast()", () => {
  it("orders roles viewer < operator < admin < owner", () => {
    expect(atLeast("owner", "admin")).toBe(true)
    expect(atLeast("admin", "admin")).toBe(true)
    expect(atLeast("operator", "admin")).toBe(false)
    expect(atLeast("viewer", "operator")).toBe(false)
  })
})

describe("Meridian Cloud console", () => {
  it("signs in with an email code, creates a workspace and lands on the overview", async () => {
    let me: Me | null = null
    const server = fakeCloud({
      "/api/me": () => (me ? json(200, me) : json(401, { detail: { code: "unauthenticated", message: "Sign in" } })),
      "POST /auth/email/start": () => json(202, { sent: true }),
      "POST /auth/email/verify": (b) => {
        const { code } = b as { code: string }
        if (code !== "424242") return json(400, { detail: { code: "invalid_code", message: "That code is incorrect." } })
        me = { user: USER, workspaces: [] }
        return json(200, { user: USER })
      },
      "POST /api/workspaces": (b) => {
        const ws = { id: "w1", slug: "acme", name: (b as { name: string }).name, role: "owner" as const }
        me = { user: USER, workspaces: [ws] }
        return json(201, ws)
      },
      "/api/w/w1/gateways": () => json(200, { gateways: [] }),
      "/api/w/w1/members": () => json(200, { members: [{ ...USER, role: "owner", joined_at: null, last_login_at: null }], invitations: [] }),
      "/api/w/w1/overview": () => json(503, { detail: { code: "unavailable", message: "warming up" } }),
    })
    vi.stubGlobal("fetch", server.impl)
    const user = userEvent.setup()
    renderCloud("/")

    expect(await screen.findByRole("heading", { name: "Sign in to Meridian" })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Continue with Google/ })).toHaveAttribute("href", expect.stringContaining("/auth/google/start"))

    await user.type(screen.getByLabelText("Work email"), "ada@acme.dev")
    await user.click(screen.getByRole("button", { name: /Continue with email/ }))
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeInTheDocument()

    await user.type(screen.getByLabelText("Sign-in code"), "424242")
    expect(await screen.findByRole("heading", { name: /Welcome, Ada/ })).toBeInTheDocument()

    await user.type(screen.getByLabelText(/Workspace name/), "Acme Inference")
    await user.click(screen.getByRole("button", { name: "Create workspace" }))
    expect(await screen.findByText("Get started")).toBeInTheDocument()
    expect(window.localStorage.getItem("meridian.cloud.workspace")).toBe("w1")

    // Every state-changing call carries the CSRF header; none carries a bearer key.
    const writes = server.calls.filter((c) => c.method !== "GET")
    expect(writes.map((c) => c.path)).toEqual(["/auth/email/start", "/auth/email/verify", "/api/workspaces"])
    for (const c of writes) expect(c.headers["X-Meridian-CSRF"]).toBe("1")
    for (const c of server.calls) expect(c.headers.Authorization).toBeUndefined()
  })

  it("returns to sign-in when the session expires mid-use", async () => {
    let signedIn = true
    const ws = { id: "w1", slug: "acme", name: "Acme", role: "admin" as const }
    vi.stubGlobal(
      "fetch",
      fakeCloud({
        "/api/me": () => (signedIn ? json(200, { user: USER, workspaces: [ws] }) : json(401, { detail: { code: "unauthenticated", message: "Sign in" } })),
        "/api/w/w1/gateways": () => (signedIn ? json(200, { gateways: [] }) : json(401, { detail: { code: "unauthenticated", message: "Sign in" } })),
        "/api/w/w1/members": () => json(200, { members: [], invitations: [] }),
      }).impl,
    )
    const { client } = renderCloud("/gateways")
    expect(await screen.findByRole("heading", { name: "Gateways" })).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /Connect gateway/ }).length).toBeGreaterThan(0)

    signedIn = false
    await client.refetchQueries({ queryKey: ["cloud", "gateways"] })
    expect(await screen.findByRole("heading", { name: "Sign in to Meridian" })).toBeInTheDocument()
    await waitFor(() => expect(client.getQueryCache().findAll({ queryKey: ["cloud", "gateways"] })).toHaveLength(0))
  })

  it("signs out from the account page", async () => {
    let signedIn = true
    const ws = { id: "w1", slug: "acme", name: "Acme", role: "owner" as const }
    const server = fakeCloud({
      "/api/me": () => (signedIn ? json(200, { user: USER, workspaces: [ws] }) : json(401, { detail: { code: "unauthenticated", message: "Sign in" } })),
      "/api/me/sessions": () => json(200, { sessions: [] }),
      "POST /auth/logout": () => {
        signedIn = false
        return json(200, { ok: true })
      },
    })
    vi.stubGlobal("fetch", server.impl)
    const user = userEvent.setup()
    renderCloud("/settings?tab=account")
    await user.click(await screen.findByRole("button", { name: "Sign out" }))
    expect(await screen.findByRole("heading", { name: "Sign in to Meridian" })).toBeInTheDocument()
    expect(server.calls.find((c) => c.path === "/auth/logout")?.headers["X-Meridian-CSRF"]).toBe("1")
  })

  it("connect dialog waits for a gateway that wasn't there when the token was minted", async () => {
    const ws = { id: "w1", slug: "acme", name: "Acme", role: "admin" as const }
    const gw = (id: string) => ({
      gateway_id: id, name: id, version: "0.12.0", online: true, first_seen_at: null, last_seen_at: null,
      strategy: "least_inflight", backends_total: 1, backends_healthy: 1, window: {}, canary: null,
    })
    let gateways = [gw("old-gw:8080")]
    vi.stubGlobal(
      "fetch",
      fakeCloud({
        "/api/me": () => json(200, { user: USER, workspaces: [ws] }),
        "/api/w/w1/gateways": () => json(200, { gateways }),
        "POST /api/w/w1/gateway-tokens": () => json(201, { token: "mgw_secret", id: "t1", name: "edge", prefix: "mgw_secr" }),
      }).impl,
    )
    const user = userEvent.setup()
    // Opened from a link: the list hasn't loaded when the dialog mounts.
    const { client } = renderCloud("/gateways?connect=1")
    await user.click(await screen.findByRole("button", { name: "Create token" }))
    expect(await screen.findByText("mgw_secret")).toBeInTheDocument()
    expect(screen.getByText(/Waiting for the first report/)).toBeInTheDocument()

    gateways = [...gateways, gw("new-gw:8080")]
    await client.refetchQueries({ queryKey: ["cloud", "gateways"] })
    expect(await screen.findByText("new-gw:8080 is connected")).toBeInTheDocument()
  })

  it("hides admin actions from viewers", async () => {
    const ws = { id: "w1", slug: "acme", name: "Acme", role: "viewer" as const }
    vi.stubGlobal(
      "fetch",
      fakeCloud({
        "/api/me": () => json(200, { user: USER, workspaces: [ws] }),
        "/api/w/w1/gateways": () => json(200, { gateways: [] }),
      }).impl,
    )
    renderCloud("/gateways")
    expect(await screen.findByText("No gateways connected")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Connect gateway/ })).not.toBeInTheDocument()
  })
})
