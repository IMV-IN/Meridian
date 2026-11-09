import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react"
import { toast } from "sonner"

import { ApiError, onUnauthorized, rawRequest } from "@/lib/api/client"
import { credentials, type Persistence } from "@/lib/api/credentials"
import { gateway } from "@/lib/api/gateway"
import type { Capabilities, CloudAccount, CloudLink, Features, Identity, WhoAmI } from "@/lib/api/types"

export type AuthPhase = "loading" | "unreachable" | "signed-out" | "ready"

export interface AuthContextValue {
  phase: AuthPhase
  whoami: WhoAmI | null
  identity: Identity | null
  /** Signed in with a Meridian Cloud account (rather than an API key). */
  account: CloudAccount | null
  /** This gateway reports to Meridian Cloud: the fleet is managed there. */
  cloud: CloudLink | null
  authEnabled: boolean
  capabilities: Capabilities
  features: Features
  error: ApiError | null
  /** Set when the stored key was rejected (rotated / deleted). */
  rejectedReason: string | null
  can: (cap: keyof Capabilities) => boolean
  signIn: (key: string, persistence: Persistence) => Promise<WhoAmI>
  signOut: () => void
  retry: () => void
}

const NO_CAPS: Capabilities = { view_ops: false, reload: false, manage_keys: false, read_all_cost: false }
const NO_FEATURES: Features = {
  cost: false,
  budgets: false,
  key_lifecycle: false,
  control_plane: false,
  canary: false,
  pii: false,
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const key = useSyncExternalStore(credentials.subscribe, credentials.get, () => null)
  const [rejectedReason, setRejectedReason] = useState<string | null>(null)

  const q = useQuery({
    queryKey: ["whoami", key ?? "anon"],
    queryFn: ({ signal }) => gateway.whoami(signal),
    retry: (count, err) => err instanceof ApiError && err.isNetwork && count < 2,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  })

  // A key that stops working mid-session (deleted / rotated) → sign out with
  // an explanation instead of leaving every view in a 401 error state.
  useEffect(
    () =>
      onUnauthorized((err) => {
        if (credentials.get()) {
          credentials.clear()
          setRejectedReason(err.message || "The gateway rejected your API key.")
          qc.clear()
          toast.error("Signed out", { description: "Your API key was rejected by the gateway." })
          return
        }
        // A Meridian Cloud session that ended (expired, or removed from the
        // workspace): show the sign-in page with the reason.
        const who = qc.getQueryData<WhoAmI>(["whoami", "anon"])
        if (who?.account) {
          setRejectedReason("Your session ended. Sign in again with Meridian Cloud.")
          qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "whoami" })
          void qc.resetQueries({ queryKey: ["whoami"] })
        }
      }),
    [qc],
  )

  const signIn = useCallback(
    async (candidate: string, persistence: Persistence) => {
      const trimmed = candidate.trim()
      // Validate before storing: whoami with the candidate key only.
      const resp = await rawRequest("/meridian/whoami", {
        auth: false,
        headers: { Authorization: `Bearer ${trimmed}` },
      })
      const who = (await resp.json()) as WhoAmI
      credentials.set(trimmed, persistence)
      setRejectedReason(null)
      qc.removeQueries()
      qc.setQueryData(["whoami", trimmed], who)
      return who
    },
    [qc],
  )

  const signOut = useCallback(() => {
    const who = qc.getQueryData<WhoAmI>(["whoami", credentials.get() ?? "anon"])
    credentials.clear()
    setRejectedReason(null)
    if (who?.account) {
      // End the gateway's Meridian Cloud session, then show the sign-in page.
      void gateway
        .cloudSignOut()
        .catch(() => undefined)
        .finally(() => {
          // Drop cached data, then re-ask who we are: the 401 shows sign-in.
          qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "whoami" })
          void qc.resetQueries({ queryKey: ["whoami"] })
        })
      return
    }
    qc.clear()
  }, [qc])

  const value = useMemo<AuthContextValue>(() => {
    const err = q.error instanceof ApiError ? q.error : q.error ? new ApiError(String(q.error), { status: 0 }) : null
    let phase: AuthPhase = "loading"
    if (q.data) phase = "ready"
    else if (err?.isUnauthorized) phase = "signed-out"
    else if (err) phase = "unreachable"
    const whoami = q.data ?? null
    const capabilities = whoami?.capabilities ?? NO_CAPS
    return {
      phase,
      whoami,
      identity: whoami?.identity ?? null,
      account: whoami?.account ?? null,
      cloud: whoami?.cloud ?? null,
      authEnabled: whoami?.auth_enabled ?? err?.isUnauthorized ?? false,
      capabilities,
      features: whoami?.features ?? NO_FEATURES,
      error: err,
      rejectedReason,
      can: (cap) => capabilities[cap],
      signIn,
      signOut,
      retry: () => void q.refetch(),
    }
  }, [q, rejectedReason, signIn, signOut])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}
