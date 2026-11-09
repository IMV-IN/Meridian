import { credentials } from "./credentials"

/**
 * Error raised for any non-2xx response or transport failure.
 * Normalizes the three envelopes the console talks to:
 *  - gateway:  {"error": {"message", "type"}}
 *  - control:  {"error": {"code", "message", "retryable"}}
 *  - FastAPI:  {"detail": ...}
 */
export class ApiError extends Error {
  readonly status: number
  readonly type: string | null
  readonly code: string | null
  readonly retryable: boolean
  readonly payload: unknown

  constructor(
    message: string,
    opts: { status: number; type?: string | null; code?: string | null; retryable?: boolean; payload?: unknown },
  ) {
    super(message)
    this.name = "ApiError"
    this.status = opts.status
    this.type = opts.type ?? null
    this.code = opts.code ?? null
    this.retryable = opts.retryable ?? false
    this.payload = opts.payload
  }

  get isNetwork() {
    return this.status === 0
  }
  get isUnauthorized() {
    return this.status === 401
  }
  get isForbidden() {
    return this.status === 403
  }
  get isNotFound() {
    return this.status === 404
  }
}

type UnauthorizedListener = (err: ApiError) => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

/** Notified when an authenticated gateway call (key or session) is rejected as 401. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  method?: "GET" | "POST" | "DELETE" | "PUT" | "PATCH"
  body?: unknown
  query?: Query
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Absolute base URL (control plane). Omit for same-origin gateway calls. */
  baseUrl?: string
  /** Attach the Meridian API key. Only ever true for the gateway origin. */
  auth?: boolean
}

export function buildUrl(path: string, query?: Query, baseUrl?: string): string {
  const base = baseUrl ? baseUrl.replace(/\/+$/, "") : ""
  const qs = new URLSearchParams()
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue
      qs.set(k, String(v))
    }
  }
  const q = qs.toString()
  return `${base}${path}${q ? `?${q}` : ""}`
}

async function parseError(resp: Response): Promise<ApiError> {
  let payload: unknown = null
  let message = `${resp.status} ${resp.statusText || "Request failed"}`.trim()
  let type: string | null = null
  let code: string | null = null
  let retryable = false
  try {
    const text = await resp.text()
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = null
  }
  if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>
    const err = p.error as Record<string, unknown> | undefined
    if (err && typeof err === "object") {
      if (typeof err.message === "string") message = err.message
      if (typeof err.type === "string") type = err.type
      if (typeof err.code === "string") code = err.code
      if (typeof err.retryable === "boolean") retryable = err.retryable
    } else if (typeof p.detail === "string") {
      message = p.detail
    } else if (Array.isArray(p.detail)) {
      message = p.detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join("; ")
    }
  }
  return new ApiError(message, { status: resp.status, type, code, retryable, payload })
}

/** Low-level fetch returning the raw Response (used for streams + downloads). */
export async function rawRequest(path: string, opts: RequestOptions = {}): Promise<Response> {
  const { method = "GET", body, query, headers = {}, signal, baseUrl, auth = true } = opts
  const finalHeaders: Record<string, string> = { Accept: "application/json", ...headers }
  const key = auth && !baseUrl ? credentials.get() : null
  if (key) finalHeaders.Authorization = `Bearer ${key}`
  // Writes on the gateway's own origin carry the CSRF header that cookie
  // (Meridian Cloud) sessions require; harmless with an API key.
  if (!baseUrl && method !== "GET") finalHeaders["X-Meridian-CSRF"] = "1"
  let payload: BodyInit | undefined
  if (body !== undefined) {
    finalHeaders["Content-Type"] = "application/json"
    payload = JSON.stringify(body)
  }
  let resp: Response
  try {
    resp = await fetch(buildUrl(path, query, baseUrl), {
      method,
      headers: finalHeaders,
      body: payload,
      signal,
      credentials: baseUrl ? "omit" : "same-origin",
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e
    throw new ApiError(
      baseUrl ? `Cannot reach ${baseUrl} — check the URL, network path and CORS settings.` : "Cannot reach the gateway.",
      { status: 0, type: "network_error" },
    )
  }
  if (!resp.ok) {
    const err = await parseError(resp)
    if (err.isUnauthorized && !baseUrl && auth) for (const l of unauthorizedListeners) l(err)
    throw err
  }
  return resp
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const resp = await rawRequest(path, opts)
  if (resp.status === 204) return undefined as T
  const text = await resp.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export async function requestText(path: string, opts: RequestOptions = {}): Promise<string> {
  const resp = await rawRequest(path, { ...opts, headers: { Accept: "text/plain", ...opts.headers } })
  return resp.text()
}
