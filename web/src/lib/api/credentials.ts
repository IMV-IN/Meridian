// Holds the operator's Meridian API key for the gateway origin only.
//
// Default persistence is the tab session (sessionStorage) — closing the tab
// signs out. Operators can opt into "remember on this device" (localStorage).
// Storage access can throw (private mode, blocked site data), so every access
// is guarded and the console keeps working with an in-memory key.

export type Persistence = "session" | "local"

const STORAGE_KEY = "meridian.console.apiKey"
// The pre-console single-file dashboard stored the key here.
const LEGACY_KEY = "meridian_api_key"

type Listener = () => void

function safeGet(store: () => Storage, key: string): string | null {
  try {
    return store().getItem(key)
  } catch {
    return null
  }
}

function safeSet(store: () => Storage, key: string, value: string | null) {
  try {
    if (value === null) store().removeItem(key)
    else store().setItem(key, value)
  } catch {
    /* storage unavailable — key stays in memory only */
  }
}

const session = () => window.sessionStorage
const local = () => window.localStorage

function load(): { key: string | null; persistence: Persistence | null } {
  const s = safeGet(session, STORAGE_KEY)
  if (s) return { key: s, persistence: "session" }
  const l = safeGet(local, STORAGE_KEY)
  if (l) return { key: l, persistence: "local" }
  const legacy = safeGet(local, LEGACY_KEY)
  if (legacy && legacy.trim()) return { key: legacy.trim(), persistence: "local" }
  return { key: null, persistence: null }
}

let state = typeof window === "undefined" ? { key: null, persistence: null } : load()
const listeners = new Set<Listener>()

function emit() {
  for (const l of listeners) l()
}

export const credentials = {
  get(): string | null {
    return state.key
  },
  persistence(): Persistence | null {
    return state.persistence
  },
  set(key: string, persistence: Persistence) {
    const trimmed = key.trim()
    safeSet(session, STORAGE_KEY, persistence === "session" ? trimmed : null)
    safeSet(local, STORAGE_KEY, persistence === "local" ? trimmed : null)
    safeSet(local, LEGACY_KEY, null)
    state = { key: trimmed, persistence }
    emit()
  },
  clear() {
    safeSet(session, STORAGE_KEY, null)
    safeSet(local, STORAGE_KEY, null)
    safeSet(local, LEGACY_KEY, null)
    state = { key: null, persistence: null }
    emit()
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
}

/** Non-secret display form: `mrdn_ab12…` */
export function maskKey(key: string | null): string {
  if (!key) return ""
  return key.length <= 13 ? key : `${key.slice(0, 13)}…`
}
