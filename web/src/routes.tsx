import { lazy } from "react"
import { createBrowserRouter, Navigate, type RouteObject } from "react-router"

import { AppShell, RequireCap } from "@/components/layout/app-shell"
import type { CrumbHandle } from "@/components/layout/site-header"
import { useAuth } from "@/providers/auth-provider"

const Overview = lazy(() => import("@/pages/overview"))
const Requests = lazy(() => import("@/pages/requests"))
const Metrics = lazy(() => import("@/pages/metrics"))
const Backends = lazy(() => import("@/pages/backends/list"))
const BackendDetail = lazy(() => import("@/pages/backends/detail"))
const Routing = lazy(() => import("@/pages/routing"))
const Playground = lazy(() => import("@/pages/playground"))
const Nodes = lazy(() => import("@/pages/fleet/nodes"))
const NodeDetail = lazy(() => import("@/pages/fleet/node-detail"))
const Enrollment = lazy(() => import("@/pages/fleet/enrollment"))
const Serving = lazy(() => import("@/pages/fleet/serving"))
const FleetAudit = lazy(() => import("@/pages/fleet/audit"))
const Keys = lazy(() => import("@/pages/keys"))
const Usage = lazy(() => import("@/pages/usage"))
const Policies = lazy(() => import("@/pages/policies"))
const Settings = lazy(() => import("@/pages/settings"))
const NotFound = lazy(() => import("@/pages/not-found"))

/** Tenant keys without operator rights land on their own usage instead of a 403. */
function Home() {
  const { can } = useAuth()
  if (!can("view_ops")) return <Navigate to="/usage" replace />
  return <Overview />
}

const crumb = (label: string): CrumbHandle => ({ crumb: () => label })

const ops = (el: React.ReactNode) => <RequireCap cap="view_ops">{el}</RequireCap>

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    handle: crumb("Console"),
    children: [
      { index: true, element: <Home />, handle: crumb("Overview") },
      { path: "requests", element: ops(<Requests />), handle: crumb("Requests") },
      { path: "metrics", element: ops(<Metrics />), handle: crumb("Observability") },
      {
        path: "backends",
        handle: crumb("Backends"),
        children: [
          { index: true, element: ops(<Backends />) },
          {
            path: ":name",
            element: ops(<BackendDetail />),
            handle: { crumb: (p) => decodeURIComponent(p.name ?? "") } satisfies CrumbHandle,
          },
        ],
      },
      { path: "routing", element: ops(<Routing />), handle: crumb("Routing & rollout") },
      { path: "playground", element: <Playground />, handle: crumb("Playground") },
      {
        path: "fleet",
        handle: crumb("Fleet"),
        children: [
          { index: true, element: ops(<Nodes />), handle: crumb("Nodes") },
          {
            path: "nodes/:nodeId",
            element: ops(<NodeDetail />),
            handle: { crumb: (p) => decodeURIComponent(p.nodeId ?? "") } satisfies CrumbHandle,
          },
          { path: "enrollment", element: ops(<Enrollment />), handle: crumb("Enrollment") },
          { path: "serving", element: ops(<Serving />), handle: crumb("Serving & placement") },
          { path: "audit", element: ops(<FleetAudit />), handle: crumb("Audit") },
        ],
      },
      {
        path: "keys",
        element: (
          <RequireCap cap="manage_keys">
            <Keys />
          </RequireCap>
        ),
        handle: crumb("API keys"),
      },
      { path: "usage", element: <Usage />, handle: crumb("Usage & cost") },
      { path: "policies", element: ops(<Policies />), handle: crumb("Policies") },
      { path: "settings", element: <Settings />, handle: crumb("Settings") },
      { path: "*", element: <NotFound />, handle: crumb("Not found") },
    ],
  },
]

export function createRouter() {
  return createBrowserRouter(routes, { basename: "/ui" })
}
