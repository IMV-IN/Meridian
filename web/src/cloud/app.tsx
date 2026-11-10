import { lazy } from "react"
import { createBrowserRouter, type RouteObject } from "react-router"

import type { CrumbHandle } from "@/components/layout/site-header"

import { CloudShell } from "./shell"

const Overview = lazy(() => import("./pages/overview"))
const Gateways = lazy(() => import("./pages/gateways"))
const GatewayDetail = lazy(() => import("./pages/gateway-detail"))
const Marketplace = lazy(() => import("./pages/marketplace"))
const Jobs = lazy(() => import("./pages/jobs"))
const JobDetail = lazy(() => import("./pages/job-detail"))
const Billing = lazy(() => import("./pages/billing"))
const Settings = lazy(() => import("./pages/settings"))
const Nodes = lazy(() => import("@/pages/fleet/nodes"))
const NodeDetail = lazy(() => import("@/pages/fleet/node-detail"))
const Enrollment = lazy(() => import("@/pages/fleet/enrollment"))
const Serving = lazy(() => import("@/pages/fleet/serving"))
const FleetAudit = lazy(() => import("@/pages/fleet/audit"))
const NotFound = lazy(() => import("@/pages/not-found"))

const crumb = (label: string): CrumbHandle => ({ crumb: () => label })

/**
 * Meridian Cloud routes. Fleet pages are shared with the self-hosted
 * console; in Cloud they talk to the current workspace's API.
 */
export const cloudRoutes: RouteObject[] = [
  {
    path: "/",
    element: <CloudShell />,
    handle: crumb("Meridian Cloud"),
    children: [
      { index: true, element: <Overview />, handle: crumb("Overview") },
      {
        path: "gateways",
        handle: crumb("Gateways"),
        children: [
          { index: true, element: <Gateways /> },
          {
            path: ":gatewayId",
            element: <GatewayDetail />,
            handle: { crumb: (p) => decodeURIComponent(p.gatewayId ?? "") } satisfies CrumbHandle,
          },
        ],
      },
      {
        path: "fleet",
        handle: crumb("Fleet"),
        children: [
          { index: true, element: <Nodes />, handle: crumb("Nodes") },
          {
            path: "nodes/:nodeId",
            element: <NodeDetail />,
            handle: { crumb: (p) => decodeURIComponent(p.nodeId ?? "") } satisfies CrumbHandle,
          },
          { path: "enrollment", element: <Enrollment />, handle: crumb("Enrollment") },
          { path: "serving", element: <Serving />, handle: crumb("Serving & placement") },
          { path: "audit", element: <FleetAudit />, handle: crumb("Audit") },
        ],
      },
      { path: "marketplace", element: <Marketplace />, handle: crumb("Marketplace") },
      {
        path: "jobs",
        handle: crumb("Jobs"),
        children: [
          { index: true, element: <Jobs /> },
          {
            path: ":jobId",
            element: <JobDetail />,
            handle: { crumb: (p) => decodeURIComponent(p.jobId ?? "") } satisfies CrumbHandle,
          },
        ],
      },
      { path: "billing", element: <Billing />, handle: crumb("Billing") },
      { path: "settings", element: <Settings />, handle: crumb("Settings") },
      { path: "*", element: <NotFound />, handle: crumb("Not found") },
    ],
  },
]

export function createCloudRouter() {
  return createBrowserRouter(cloudRoutes, { basename: "/ui" })
}
