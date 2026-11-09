import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError, buildUrl, onUnauthorized, request } from "./client"
import { credentials } from "./credentials"
import { chatStream } from "./gateway"

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } })
}

describe("request()", () => {
  const fetchMock = vi.fn<typeof fetch>()
  const call = (i: number) => {
    const [url, init] = fetchMock.mock.calls[i] ?? []
    return { url, init: (init ?? {}) as RequestInit & { headers: Record<string, string> } }
  }
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock)
    credentials.clear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    fetchMock.mockReset()
    credentials.clear()
  })

  it("attaches the bearer key to same-origin gateway calls only", async () => {
    credentials.set("mrdn_testkeytestkeytestkey1", "session")
    fetchMock.mockImplementation(async () => jsonResponse(200, { ok: true }))
    await request("/meridian/status")
    expect(call(0).init.headers.Authorization).toBe("Bearer mrdn_testkeytestkeytestkey1")

    // Control plane (absolute base URL) must never receive the Meridian key.
    await request("/admin/nodes", { baseUrl: "http://control:8443", auth: false })
    expect(call(1).url).toBe("http://control:8443/admin/nodes")
    expect(call(1).init.headers.Authorization).toBeUndefined()
    expect(call(1).init.credentials).toBe("omit")
    await request("/admin/nodes", { baseUrl: "http://control:8443" })
    expect(call(2).init.headers.Authorization).toBeUndefined()
  })

  it("normalizes gateway, control-plane and FastAPI error envelopes", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { error: { message: "nope", type: "permission_error" } }))
    await expect(request("/x")).rejects.toMatchObject({ status: 403, type: "permission_error", message: "nope" })

    fetchMock.mockResolvedValueOnce(jsonResponse(404, { error: { code: "NODE_NOT_FOUND", message: "unknown node", retryable: false } }))
    await expect(request("/y")).rejects.toMatchObject({ status: 404, code: "NODE_NOT_FOUND", message: "unknown node" })

    fetchMock.mockResolvedValueOnce(jsonResponse(422, { detail: [{ msg: "field required" }, { msg: "bad type" }] }))
    await expect(request("/z")).rejects.toMatchObject({ status: 422, message: "field required; bad type" })
  })

  it("maps transport failures to a network ApiError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"))
    const err = await request("/x").catch((e: ApiError) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).isNetwork).toBe(true)
  })

  it("notifies unauthorized listeners for the gateway's own calls only", async () => {
    // Key or Meridian Cloud session: the auth provider decides what ended.
    const seen: number[] = []
    const off = onUnauthorized((e) => seen.push(e.status))
    fetchMock.mockImplementation(async () => jsonResponse(401, { error: { message: "Invalid API key", type: "authentication_error" } }))
    await request("/meridian/status").catch(() => undefined)
    expect(seen).toEqual([401])
    await request("/admin/nodes", { baseUrl: "http://control:8443" }).catch(() => undefined)
    await request("/meridian/console/sign-in-options", { auth: false }).catch(() => undefined)
    expect(seen).toEqual([401]) // other origins and unauthenticated calls don't sign anyone out
    off()
  })

  it("sends the CSRF header on same-origin writes only", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, {}))
    await request("/meridian/reload", { method: "POST" })
    await request("/meridian/status")
    await request("/admin/x", { method: "POST", baseUrl: "http://control:8443" })
    const headers = fetchMock.mock.calls.map((c) => (c[1] as RequestInit).headers as Record<string, string>)
    expect(headers[0]["X-Meridian-CSRF"]).toBe("1")
    expect(headers[1]["X-Meridian-CSRF"]).toBeUndefined()
    expect(headers[2]["X-Meridian-CSRF"]).toBeUndefined()
  })

  it("builds URLs and drops empty query values", () => {
    expect(buildUrl("/meridian/usage", { org: "acme", team: "", key: undefined, window_days: 7 })).toBe("/meridian/usage?org=acme&window_days=7")
    expect(buildUrl("/admin/x", undefined, "http://c:1/")).toBe("http://c:1/admin/x")
  })
})

describe("chatStream()", () => {
  afterEach(() => vi.unstubAllGlobals())

  function sseResponse(chunks: string[], headers: Record<string, string> = {}) {
    const enc = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const ch of chunks) c.enqueue(enc.encode(ch))
        c.close()
      },
    })
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream", ...headers } })
  }

  it("reassembles events split across chunks and reads Meridian headers + usage", async () => {
    const delta = (t: string) => `data: ${JSON.stringify({ choices: [{ delta: { content: t } }] })}\n\n`
    const whole = delta("Hel") + delta("lo") + `data: ${JSON.stringify({ choices: [], usage: { prompt_tokens: 3, completion_tokens: 2 } })}\n\n` + "data: [DONE]\n\n"
    // Split mid-event to exercise buffering.
    const chunks = [whole.slice(0, 17), whole.slice(17, 60), whole.slice(60)]
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse(chunks, { "x-request-id": "mrdn-1", "x-meridian-backend": "fast", "x-meridian-tier": "default" })),
    )
    const deltas: string[] = []
    const r = await chatStream({ model: "m", messages: [] }, { onDelta: (c) => deltas.push(c) })
    expect(r.content).toBe("Hello")
    expect(deltas).toEqual(["Hel", "Hello"])
    expect(r.usage).toEqual({ prompt_tokens: 3, completion_tokens: 2 })
    expect(r.meta).toMatchObject({ requestId: "mrdn-1", backend: "fast", tier: "default", status: 200 })
    expect(r.ttftMs).not.toBeNull()
  })

  it("surfaces an in-stream gateway error event", async () => {
    const err = `data: ${JSON.stringify({ error: { message: "upstream died", type: "upstream_5xx" } })}\n\ndata: [DONE]\n\n`
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([err])))
    await expect(chatStream({ model: "m", messages: [] }, {})).rejects.toMatchObject({ type: "upstream_5xx", message: "upstream died" })
  })
})
