import { TriangleAlert } from "lucide-react"
import { Suspense, useEffect, type ReactNode } from "react"
import { Link, Outlet, useLocation, useMatches, type UIMatch } from "react-router"

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import type { Capabilities } from "@/lib/api/types"
import { BootScreen, SignInPage, UnreachablePage } from "@/pages/sign-in"
import { useAuth } from "@/providers/auth-provider"
import { TelemetryProvider } from "@/providers/telemetry-provider"

import { PermissionState } from "../states"
import { AppSidebar } from "./app-sidebar"
import { CommandMenu, useCommandMenu } from "./command-menu"
import { SiteHeader, type CrumbHandle } from "./site-header"

function PageFallback() {
  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-6 p-4 md:p-6 lg:px-8">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-80 rounded-xl" />
    </div>
  )
}

function OpenModeBanner() {
  const { authEnabled, phase } = useAuth()
  if (authEnabled || phase !== "ready") return null
  return (
    <div className="flex items-center gap-2 border-b bg-[color-mix(in_oklab,var(--status-warning)_12%,transparent)] px-4 py-1.5 text-xs md:rounded-t-xl">
      <TriangleAlert className="size-3.5 shrink-0 text-(--status-warning)" />
      <span>
        <b>Open mode</b> — gateway auth is disabled, so operator views are readable by anyone who can reach this port. Key
        management and config reload need <code className="font-mono">auth.enabled: true</code>.{" "}
        <Link to="/settings" className="underline underline-offset-2">
          Learn more
        </Link>
      </span>
    </div>
  )
}

/** Browser tab title follows the deepest breadcrumb: "Backends · Meridian". */
function useDocumentTitle() {
  const matches = useMatches() as UIMatch<unknown, CrumbHandle>[]
  const last = [...matches].reverse().find((m) => m.handle?.crumb)
  const label = last?.handle.crumb?.(last.params)
  useEffect(() => {
    document.title = label && label !== "Console" ? `${label} · Meridian` : "Meridian Console"
  }, [label])
}

function Shell() {
  const { open, setOpen } = useCommandMenu()
  const { pathname } = useLocation()
  useDocumentTitle()
  return (
    <TelemetryProvider>
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset className="min-w-0">
          <SiteHeader onOpenCommand={() => setOpen(true)} />
          <OpenModeBanner />
          <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
            <Suspense key={pathname} fallback={<PageFallback />}>
              <Outlet />
            </Suspense>
          </main>
        </SidebarInset>
        <CommandMenu open={open} onOpenChange={setOpen} />
      </SidebarProvider>
    </TelemetryProvider>
  )
}

/** Boot → (unreachable | sign-in | shell). */
export function AppShell() {
  const { phase, error, retry } = useAuth()
  if (phase === "loading") return <BootScreen />
  if (phase === "unreachable") return <UnreachablePage error={error} onRetry={retry} />
  if (phase === "signed-out") return <SignInPage />
  return <Shell />
}

const CAP_LABEL: Record<keyof Capabilities, string> = {
  view_ops: "an operator-view key (role viewer, operator or admin)",
  reload: "an operator or admin key",
  manage_keys: "an admin key (role admin or ops_admin)",
  read_all_cost: "a finance or admin key",
}

/** Route-level guard: show an explanation instead of a wall of 403s. */
export function RequireCap({ cap, children }: { cap: keyof Capabilities; children: ReactNode }) {
  const { can } = useAuth()
  if (!can(cap))
    return (
      <div className="mx-auto w-full max-w-3xl p-6 md:p-10">
        <PermissionState need={CAP_LABEL[cap]} />
      </div>
    )
  return <>{children}</>
}
