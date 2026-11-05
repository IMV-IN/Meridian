import { Bell, BellOff, Moon, Pause, Play, Search, Sun } from "lucide-react"
import { useTheme } from "@/providers/theme-provider"
import { Fragment, useEffect, useState } from "react"
import { Link, useMatches, useNavigate, type UIMatch } from "react-router"

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useStatus } from "@/hooks/queries"
import type { ConsoleEvent } from "@/lib/telemetry"
import { cn } from "@/lib/utils"
import { useAuth } from "@/providers/auth-provider"
import { useSettings } from "@/providers/settings-provider"
import { useTelemetry } from "@/providers/telemetry-provider"

import { StatusDot, type Tone } from "../status-badge"
import { TimeAgo } from "../misc"

export interface CrumbHandle {
  crumb?: (params: Record<string, string | undefined>) => string
}

function Crumbs() {
  const matches = useMatches() as UIMatch<unknown, CrumbHandle>[]
  const crumbs = matches
    .filter((m) => m.handle?.crumb)
    .map((m) => ({ label: m.handle.crumb!(m.params), to: m.pathname }))
  return (
    <Breadcrumb className="min-w-0">
      <BreadcrumbList className="flex-nowrap">
        {crumbs.map((c, i) => (
          <Fragment key={c.to + i}>
            {i > 0 && <BreadcrumbSeparator className="hidden sm:block" />}
            <BreadcrumbItem className={cn("min-w-0", i < crumbs.length - 1 && "hidden sm:inline-flex")}>
              {i === crumbs.length - 1 ? (
                <BreadcrumbPage className="truncate font-medium">{c.label}</BreadcrumbPage>
              ) : (
                <BreadcrumbLink asChild>
                  <Link to={c.to}>{c.label}</Link>
                </BreadcrumbLink>
              )}
            </BreadcrumbItem>
          </Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  )
}

function LiveToggle() {
  const { settings, update } = useSettings()
  const { can } = useAuth()
  const status = useStatus()
  const live = settings.live
  const failing = can("view_ops") && status.isError
  const tone: Tone = !live ? "muted" : failing ? "critical" : "good"
  const label = !live ? "Paused" : failing ? "Disconnected" : "Live"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="gap-2 px-2 text-xs"
          onClick={() => update({ live: !live })}
          aria-pressed={live}
          aria-label={live ? "Pause live updates" : "Resume live updates"}
        >
          <StatusDot tone={tone} pulse={live && !failing} />
          <span className="hidden sm:inline">{label}</span>
          {live ? <Pause className="size-3 opacity-60" /> : <Play className="size-3 opacity-60" />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {live ? `Refreshing every ${settings.refreshMs / 1000}s — click to pause` : "Live updates paused — click to resume"}
      </TooltipContent>
    </Tooltip>
  )
}

const SEVERITY_TONE: Record<ConsoleEvent["severity"], Tone> = {
  good: "good",
  info: "info",
  warning: "warning",
  critical: "critical",
}

function ActivityFeed() {
  const { events, unread, markRead, clearEvents, since } = useTelemetry()
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  useEffect(() => {
    if (open) markRead()
  }, [open, events.length, markRead])
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon-sm" className="relative" aria-label={`Activity (${unread} new)`}>
              <Bell />
              {unread > 0 && (
                <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-amber px-1 text-[10px] font-semibold text-brand-ink">
                  {unread > 9 ? "9+" : unread}
                </span>
              )}
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Activity</TooltipContent>
      </Tooltip>
      <PopoverContent align="end" className="w-[380px] p-0">
        <div className="flex items-center justify-between border-b px-3 py-2.5">
          <div>
            <div className="text-sm font-semibold">Activity</div>
            <div className="text-xs text-muted-foreground">
              Changes detected since {since ? <TimeAgo value={since} /> : "the console opened"}
            </div>
          </div>
          {events.length > 0 && (
            <Button variant="ghost" size="xs" onClick={clearEvents}>
              Clear
            </Button>
          )}
        </div>
        {events.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
            <BellOff className="size-5 text-muted-foreground" />
            <div className="text-sm font-medium">All quiet</div>
            <p className="text-xs text-muted-foreground">
              Backend health flips, circuit trips and canary moves appear here as they happen.
            </p>
          </div>
        ) : (
          <ScrollArea className="max-h-96">
            <ul className="divide-y">
              {events.map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    className="flex w-full items-start gap-3 px-3 py-2.5 text-left hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
                    onClick={() => {
                      if (e.href) {
                        navigate(e.href)
                        setOpen(false)
                      }
                    }}
                  >
                    <span className="mt-1.5">
                      <StatusDot tone={SEVERITY_TONE[e.severity]} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium">{e.title}</span>
                      {e.detail && <span className="block text-xs text-muted-foreground">{e.detail}</span>}
                    </span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      <TimeAgo value={e.t} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </ScrollArea>
        )}
      </PopoverContent>
    </Popover>
  )
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setTheme(dark ? "light" : "dark")}
          aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
        >
          {dark ? <Sun /> : <Moon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{dark ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  )
}

export function SiteHeader({ onOpenCommand }: { onOpenCommand: () => void }) {
  const { can } = useAuth()
  return (
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/70 md:rounded-t-xl">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mx-1 data-vertical:h-4 data-vertical:self-center" />
      <Crumbs />
      <div className="ml-auto flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          className="hidden h-8 w-56 justify-start gap-2 bg-card/60 text-muted-foreground md:flex"
          onClick={onOpenCommand}
        >
          <Search className="size-3.5" />
          <span className="text-xs">Search or jump to…</span>
          <Kbd className="ml-auto">⌘K</Kbd>
        </Button>
        <Button variant="ghost" size="icon-sm" className="md:hidden" onClick={onOpenCommand} aria-label="Search">
          <Search />
        </Button>
        {can("view_ops") && <LiveToggle />}
        {can("view_ops") && <ActivityFeed />}
        <ThemeToggle />
      </div>
    </header>
  )
}
