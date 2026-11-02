import {
  ChartLine,
  Coins,
  Cpu,
  FileClock,
  KeyRound,
  LayoutDashboard,
  MessageSquareText,
  Route,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  Ticket,
  Waypoints,
  type LucideIcon,
} from "lucide-react"

import type { Capabilities } from "@/lib/api/types"

export interface NavItem {
  title: string
  to: string
  icon: LucideIcon
  /** Capability the gateway requires for the data behind this page. */
  need?: keyof Capabilities
  keywords?: string[]
  description: string
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV: NavGroup[] = [
  {
    label: "Monitor",
    items: [
      {
        title: "Overview",
        to: "/",
        icon: LayoutDashboard,
        need: "view_ops",
        keywords: ["home", "dashboard", "health"],
        description: "Fleet health, live traffic and latency at a glance",
      },
      {
        title: "Requests",
        to: "/requests",
        icon: ScrollText,
        need: "view_ops",
        keywords: ["log", "traffic", "errors", "request id"],
        description: "Recent request log with filters and drill-down",
      },
      {
        title: "Observability",
        to: "/metrics",
        icon: ChartLine,
        need: "view_ops",
        keywords: ["prometheus", "metrics", "latency", "histogram"],
        description: "Prometheus counters, latency distributions, rejections",
      },
    ],
  },
  {
    label: "Traffic",
    items: [
      {
        title: "Backends",
        to: "/backends",
        icon: Server,
        need: "view_ops",
        keywords: ["engines", "vllm", "ollama", "circuit", "telemetry"],
        description: "Inference backends, health, load and telemetry",
      },
      {
        title: "Routing & rollout",
        to: "/routing",
        icon: Route,
        need: "view_ops",
        keywords: ["strategy", "canary", "tiering", "affinity", "isolation"],
        description: "Strategy, tiering, session affinity, canary and isolation",
      },
      {
        title: "Playground",
        to: "/playground",
        icon: MessageSquareText,
        keywords: ["chat", "test", "completion", "stream"],
        description: "Send a chat completion through the gateway",
      },
    ],
  },
  {
    label: "Fleet",
    items: [
      {
        title: "Nodes",
        to: "/fleet",
        icon: Cpu,
        need: "view_ops",
        keywords: ["gpu", "hosts", "agents", "control plane"],
        description: "GPU hosts managed by meridian-control",
      },
      {
        title: "Enrollment",
        to: "/fleet/enrollment",
        icon: Ticket,
        need: "view_ops",
        keywords: ["token", "claims", "approve", "join"],
        description: "Mint join tokens and approve pending nodes",
      },
      {
        title: "Serving & placement",
        to: "/fleet/serving",
        icon: Waypoints,
        need: "view_ops",
        keywords: ["projection", "placement", "crl", "vram"],
        description: "Routable managed engines, placement and revocations",
      },
      {
        title: "Fleet audit",
        to: "/fleet/audit",
        icon: FileClock,
        need: "view_ops",
        keywords: ["events", "history"],
        description: "Control-plane audit trail",
      },
    ],
  },
  {
    label: "Governance",
    items: [
      {
        title: "API keys",
        to: "/keys",
        icon: KeyRound,
        need: "manage_keys",
        keywords: ["credentials", "rbac", "roles", "create key"],
        description: "Issue and revoke gateway API keys",
      },
      {
        title: "Usage & cost",
        to: "/usage",
        icon: Coins,
        keywords: ["tokens", "spend", "billing", "csv", "export"],
        description: "Token usage and cost attribution by org, team, key",
      },
      {
        title: "Policies",
        to: "/policies",
        icon: ShieldCheck,
        need: "view_ops",
        keywords: ["budgets", "rate limits", "pii", "quotas", "compliance"],
        description: "Budgets, rate limits and PII protection",
      },
    ],
  },
]

export const SETTINGS_NAV: NavItem = {
  title: "Settings",
  to: "/settings",
  icon: Settings,
  keywords: ["preferences", "theme", "connection", "reload", "control plane url"],
  description: "Connection, preferences and gateway operations",
}

export const ALL_NAV: NavItem[] = [...NAV.flatMap((g) => g.items), SETTINGS_NAV]
