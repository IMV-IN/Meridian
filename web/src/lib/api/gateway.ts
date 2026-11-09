import { ApiError, buildUrl, rawRequest, request, requestText } from "./client"
import type {
  ApiKeyView,
  BudgetSnapshot,
  ChatRequestBody,
  ConfigSummary,
  GatewayStatus,
  KeyCreateInput,
  KeyCreateResult,
  MeridianResponseMeta,
  ModelList,
  ReloadReport,
  RequestRecord,
  SignInOptions,
  UsageQuery,
  UsageReport,
  VersionInfo,
  WhoAmI,
} from "./types"

export const gateway = {
  whoami: (signal?: AbortSignal) => request<WhoAmI>("/meridian/whoami", { signal }),
  signInOptions: (signal?: AbortSignal) =>
    request<SignInOptions>("/meridian/console/sign-in-options", { signal, auth: false }),
  cloudSignOut: () => request<{ signed_out: boolean }>("/auth/cloud/logout", { method: "POST", auth: false }),
  version: (signal?: AbortSignal) => request<VersionInfo>("/meridian/version", { signal, auth: false }),
  status: (signal?: AbortSignal) => request<GatewayStatus>("/meridian/status", { signal }),
  requests: (signal?: AbortSignal) =>
    request<{ requests: RequestRecord[] }>("/meridian/requests", { signal }).then((r) => r.requests),
  config: (signal?: AbortSignal) => request<ConfigSummary>("/meridian/config", { signal }),
  budgets: (signal?: AbortSignal) => request<BudgetSnapshot>("/meridian/budgets", { signal }),
  metrics: (signal?: AbortSignal) => requestText("/metrics", { signal, auth: false }),
  models: (signal?: AbortSignal) => request<ModelList>("/v1/models", { signal }),
  reload: () => request<ReloadReport>("/meridian/reload", { method: "POST" }),

  listKeys: (signal?: AbortSignal) =>
    request<{ keys: ApiKeyView[] }>("/meridian/keys", { signal }).then((r) => r.keys),
  createKey: (input: KeyCreateInput) =>
    request<KeyCreateResult>("/meridian/keys", { method: "POST", body: input }),
  deleteKey: (keyId: string) =>
    request<ApiKeyView & { deleted: boolean }>(`/meridian/keys/${encodeURIComponent(keyId)}`, {
      method: "DELETE",
    }),

  usage: (q: UsageQuery, signal?: AbortSignal) =>
    request<UsageReport>("/meridian/usage", { query: { ...q }, signal }),
  /** Authenticated CSV download (a plain <a href> cannot send the bearer key). */
  async downloadUsageCsv(q: UsageQuery): Promise<void> {
    const resp = await rawRequest("/meridian/usage.csv", { query: { ...q }, headers: { Accept: "text/csv" } })
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `meridian_usage_${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}

export function readMeta(resp: Response): MeridianResponseMeta {
  const h = resp.headers
  return {
    requestId: h.get("x-request-id"),
    backend: h.get("x-meridian-backend"),
    tier: h.get("x-meridian-tier"),
    sessionRoute: h.get("x-meridian-session-route"),
    budgetRemainingTokens: h.get("x-meridian-budget-remaining-tokens"),
    budgetRemainingRequests: h.get("x-meridian-budget-remaining-requests"),
    status: resp.status,
  }
}

export interface ChatUsage {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  prompt_tokens_details?: { cached_tokens?: number }
}

export interface ChatResult {
  meta: MeridianResponseMeta
  content: string
  finishReason: string | null
  usage: ChatUsage | null
  raw: unknown
  ttftMs: number | null
  totalMs: number
  chunks: number
}

export interface StreamHandlers {
  onMeta?: (meta: MeridianResponseMeta) => void
  onDelta?: (text: string, chunks: number) => void
}

function chatHeaders(sessionId?: string, sessionHeader = "x-meridian-session"): Record<string, string> {
  return sessionId ? { [sessionHeader]: sessionId } : {}
}

/** Non-stream chat completion. Keeps the gateway error envelope on failure. */
export async function chatOnce(
  body: ChatRequestBody,
  opts: { signal?: AbortSignal; sessionId?: string; sessionHeader?: string } = {},
): Promise<ChatResult> {
  const started = performance.now()
  const resp = await rawRequest("/v1/chat/completions", {
    method: "POST",
    body: { ...body, stream: false },
    signal: opts.signal,
    headers: chatHeaders(opts.sessionId, opts.sessionHeader),
  })
  const raw = (await resp.json()) as {
    choices?: { message?: { content?: string }; finish_reason?: string }[]
    usage?: ChatUsage
  }
  const totalMs = performance.now() - started
  return {
    meta: readMeta(resp),
    content: raw.choices?.[0]?.message?.content ?? "",
    finishReason: raw.choices?.[0]?.finish_reason ?? null,
    usage: raw.usage ?? null,
    raw,
    ttftMs: null,
    totalMs,
    chunks: 1,
  }
}

/**
 * Streamed chat completion over SSE. Parses `data:` events incrementally and
 * stops at `[DONE]`. Gateway-side stream errors arrive as an `error` event
 * before `[DONE]` and are surfaced as an ApiError.
 */
export async function chatStream(
  body: ChatRequestBody,
  handlers: StreamHandlers,
  opts: { signal?: AbortSignal; sessionId?: string; sessionHeader?: string } = {},
): Promise<ChatResult> {
  const started = performance.now()
  const resp = await rawRequest("/v1/chat/completions", {
    method: "POST",
    body: { ...body, stream: true, stream_options: { include_usage: true } },
    signal: opts.signal,
    headers: { Accept: "text/event-stream", ...chatHeaders(opts.sessionId, opts.sessionHeader) },
  })
  const meta = readMeta(resp)
  handlers.onMeta?.(meta)
  if (!resp.body) throw new ApiError("Stream had no body", { status: resp.status })

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let content = ""
  let finishReason: string | null = null
  let usage: ChatUsage | null = null
  let ttftMs: number | null = null
  let chunks = 0
  let last: unknown = null
  let done = false

  const handleEvent = (data: string) => {
    if (data === "[DONE]") {
      done = true
      return
    }
    let evt: Record<string, unknown>
    try {
      evt = JSON.parse(data)
    } catch {
      return
    }
    last = evt
    if (evt.error && typeof evt.error === "object") {
      const e = evt.error as { message?: string; type?: string }
      throw new ApiError(e.message ?? "Upstream stream error", { status: 502, type: e.type ?? null, payload: evt })
    }
    const choices = evt.choices as { delta?: { content?: string }; finish_reason?: string | null }[] | undefined
    const delta = choices?.[0]?.delta?.content
    if (delta) {
      if (ttftMs === null) ttftMs = performance.now() - started
      content += delta
      chunks += 1
      handlers.onDelta?.(content, chunks)
    }
    if (choices?.[0]?.finish_reason) finishReason = choices[0].finish_reason
    if (evt.usage && typeof evt.usage === "object") usage = evt.usage as ChatUsage
  }

  while (!done) {
    const { value, done: streamDone } = await reader.read()
    if (streamDone) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    // SSE events are separated by a blank line.
    while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep).replace(/^\r?\n\r?\n/, "")
      const data = rawEvent
        .split(/\r?\n/)
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n")
      if (data) handleEvent(data)
      if (done) break
    }
  }
  if (!done && buffer.trim().startsWith("data:")) handleEvent(buffer.trim().slice(5).trim())
  try {
    await reader.cancel()
  } catch {
    /* already closed */
  }

  return {
    meta,
    content,
    finishReason,
    usage,
    raw: last,
    ttftMs,
    totalMs: performance.now() - started,
    chunks,
  }
}

/** Absolute URL for copy-paste snippets (curl / SDK). */
export function gatewayOrigin(): string {
  return typeof window === "undefined" ? "http://localhost:8080" : window.location.origin
}

export { buildUrl }
