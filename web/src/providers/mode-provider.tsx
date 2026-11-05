import { createContext, useContext } from "react"

/**
 * Which backend the console is attached to:
 * - "gateway": served by a Meridian gateway; API-key sign-in; fleet pages talk
 *   to a standalone meridian-control (/admin) configured in settings.
 * - "cloud": served by Meridian Cloud (meridian-control in SaaS mode); Google /
 *   email sign-in; fleet pages are scoped to the current workspace.
 */
export interface ModeValue {
  mode: "gateway" | "cloud"
  workspaceId?: string
  /** URL GPU nodes enroll against (the mTLS listener), for install snippets. */
  nodeUrl?: string
  role?: "owner" | "admin" | "operator" | "viewer"
}

const ModeContext = createContext<ModeValue>({ mode: "gateway" })

export const ModeProvider = ModeContext.Provider

export function useMode(): ModeValue {
  return useContext(ModeContext)
}
