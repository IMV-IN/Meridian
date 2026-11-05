import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"

import { ApiError } from "@/lib/api/client"
import type { Capabilities, Features } from "@/lib/api/types"
import { AuthContext, type AuthContextValue } from "@/providers/auth-provider"
import { ModeProvider } from "@/providers/mode-provider"

import { cloud, type CloudInfo, type Me, type WorkspaceRef } from "./api"

const WS_KEY = "meridian.cloud.workspace"

export type CloudPhase = "loading" | "signed-out" | "unreachable" | "onboarding" | "ready"

interface CloudSession {
  info: CloudInfo
  phase: CloudPhase
  me: Me | null
  workspace: WorkspaceRef | null
  error: ApiError | null
  setWorkspace: (id: string) => void
  refresh: () => Promise<unknown>
  signOut: () => Promise<void>
}

const CloudSessionContext = createContext<CloudSession | null>(null)

function readStoredWorkspace(): string | null {
  try {
    return window.localStorage.getItem(WS_KEY)
  } catch {
    return null
  }
}

// Fleet pages shared with the gateway console read capabilities through
// useAuth(). In Cloud, gateway-only views are off (there is no local gateway
// to query); authorization is the workspace role, enforced server-side.
const NO_CAPS: Capabilities = { view_ops: false, reload: false, manage_keys: false, read_all_cost: false }
const NO_FEATURES: Features = { cost: false, budgets: false, key_lifecycle: false, control_plane: true, canary: false, pii: false }

export function CloudSessionProvider({ info, children }: { info: CloudInfo; children: ReactNode }) {
  const qc = useQueryClient()
  const [stored, setStored] = useState<string | null>(readStoredWorkspace)
  const me = useQuery({
    queryKey: ["cloud", "me"],
    queryFn: ({ signal }) => cloud.me(signal),
    retry: (n, e) => e instanceof ApiError && e.isNetwork && n < 2,
    staleTime: 30_000,
  })

  const setWorkspace = useCallback(
    (id: string) => {
      try {
        window.localStorage.setItem(WS_KEY, id)
      } catch {
        /* per-viewer convenience */
      }
      setStored(id)
      void qc.invalidateQueries({ queryKey: ["control"] })
    },
    [qc],
  )

  const signOut = useCallback(async () => {
    try {
      await cloud.logout()
    } finally {
      qc.clear()
      await qc.invalidateQueries({ queryKey: ["cloud", "me"] })
    }
  }, [qc])

  const value = useMemo<CloudSession>(() => {
    const err = me.error instanceof ApiError ? me.error : me.error ? new ApiError(String(me.error), { status: 0 }) : null
    const data = me.data ?? null
    const workspace = data ? (data.workspaces.find((w) => w.id === stored) ?? data.workspaces[0] ?? null) : null
    // A 401 wins over cached data: the session expired or was revoked
    // elsewhere. Other errors keep showing cached data (transient outage).
    let phase: CloudPhase = "loading"
    if (err?.isUnauthorized) phase = "signed-out"
    else if (data) phase = workspace ? "ready" : "onboarding"
    else if (err) phase = "unreachable"
    return { info, phase, me: data, workspace, error: err, setWorkspace, refresh: () => me.refetch(), signOut }
  }, [info, me, stored, setWorkspace, signOut])

  const auth = useMemo<AuthContextValue>(
    () => ({
      phase: "ready",
      whoami: null,
      identity: null,
      authEnabled: true,
      capabilities: NO_CAPS,
      features: NO_FEATURES,
      error: null,
      rejectedReason: null,
      can: () => false,
      signIn: () => Promise.reject(new Error("API-key sign-in is not used in Meridian Cloud")),
      signOut: () => void signOut(),
      retry: () => void me.refetch(),
    }),
    [signOut, me],
  )

  return (
    <CloudSessionContext.Provider value={value}>
      <ModeProvider
        value={{ mode: "cloud", workspaceId: value.workspace?.id, nodeUrl: info.node_url, role: value.workspace?.role }}
      >
        <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
      </ModeProvider>
    </CloudSessionContext.Provider>
  )
}

export function useCloud(): CloudSession {
  const ctx = useContext(CloudSessionContext)
  if (!ctx) throw new Error("useCloud must be used inside <CloudSessionProvider>")
  return ctx
}

/** The current workspace; only valid under the signed-in shell. */
export function useWorkspace(): WorkspaceRef {
  const { workspace } = useCloud()
  if (!workspace) throw new Error("no workspace selected")
  return workspace
}
