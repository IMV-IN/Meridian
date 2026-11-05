import type { Capabilities, Role } from "./api/types"

/** Mirror of IdentityContext.can_* (meridian/auth/models.py). Keep in sync. */
export function capabilitiesFor(k: { role: Role | null; cost_admin: boolean; ops_admin: boolean }): Capabilities {
  const role = k.role
  return {
    view_ops: role === "viewer" || role === "operator" || role === "admin" || k.ops_admin || k.cost_admin,
    reload: k.ops_admin || role === "operator" || role === "admin",
    manage_keys: k.ops_admin || role === "admin",
    read_all_cost: k.cost_admin || role === "admin",
  }
}
