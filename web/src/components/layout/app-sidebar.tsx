import { ChevronsUpDown, Cloud, ExternalLink, KeyRound, Lock, LogOut, Monitor, Moon, Settings, Sun, UserRound } from "lucide-react"
import { useTheme } from "@/providers/theme-provider"
import { Link, NavLink, useLocation } from "react-router"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { useClaims, useStatus, useVersion } from "@/hooks/queries"
import { backendHealth } from "@/lib/telemetry"
import { useAuth } from "@/providers/auth-provider"

import { MeridianMark } from "./logo"
import { NAV, SETTINGS_NAV, type NavItem } from "./nav"

const FLEET_PATHS = new Set(["/fleet", "/fleet/enrollment", "/fleet/serving", "/fleet/audit"])

function isActive(pathname: string, to: string) {
  if (to === "/") return pathname === "/"
  if (to === "/fleet") return pathname === "/fleet" || pathname.startsWith("/fleet/nodes")
  return pathname === to || pathname.startsWith(`${to}/`)
}

function NavBadge({ item }: { item: NavItem }) {
  const status = useStatus()
  const pending = useClaims("pending")
  if (item.to === "/backends" && status.data) {
    const bad = status.data.backends.filter((b) => {
      const h = backendHealth(b)
      return h === "unhealthy" || h === "circuit_open"
    }).length
    if (bad > 0)
      return (
        <SidebarMenuBadge className="bg-[color-mix(in_oklab,var(--status-critical)_16%,transparent)] text-foreground">
          {bad}
        </SidebarMenuBadge>
      )
  }
  if (item.to === "/fleet/enrollment" && pending.data && pending.data.length > 0) {
    return (
      <SidebarMenuBadge className="bg-[color-mix(in_oklab,var(--status-warning)_22%,transparent)] text-foreground">
        {pending.data.length}
      </SidebarMenuBadge>
    )
  }
  return null
}

export function AppSidebar() {
  const { pathname } = useLocation()
  const { can, authEnabled, cloud } = useAuth()
  const version = useVersion()
  const { setOpenMobile } = useSidebar()

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild className="hover:bg-transparent active:bg-transparent">
              <Link to="/" onClick={() => setOpenMobile(false)}>
                <MeridianMark className="size-8" />
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-semibold tracking-tight">Meridian</span>
                  <span className="truncate text-[11px] text-sidebar-foreground/75">
                    Inference console{version.data ? ` · v${version.data.version}` : ""}
                  </span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <nav aria-label="Primary" className="flex min-h-0 flex-1 flex-col">
        <SidebarContent className="scrollbar-thin">
        {NAV.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const locked = item.need ? !can(item.need) : false
                  // With Meridian Cloud, the fleet is managed there: link out.
                  const external = cloud && FLEET_PATHS.has(item.to) ? `${cloud.url}/ui${item.to}` : null
                  if (external)
                    return (
                      <SidebarMenuItem key={item.to}>
                        <SidebarMenuButton asChild tooltip={`${item.title} (Meridian Cloud)`}>
                          <a href={external} target="_blank" rel="noopener noreferrer">
                            <item.icon />
                            <span>{item.title}</span>
                            <ExternalLink className="ml-auto size-3 opacity-60" aria-label="opens Meridian Cloud in a new tab" />
                          </a>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  return (
                    <SidebarMenuItem key={item.to}>
                      <SidebarMenuButton
                        asChild
                        isActive={isActive(pathname, item.to)}
                        tooltip={locked ? `${item.title} (restricted)` : item.title}
                        className={locked ? "text-sidebar-foreground/45" : undefined}
                      >
                        <NavLink to={item.to} end={item.to === "/"} onClick={() => setOpenMobile(false)}>
                          <item.icon />
                          <span>{item.title}</span>
                          {locked && <Lock className="ml-auto size-3 opacity-60" aria-label="restricted" />}
                        </NavLink>
                      </SidebarMenuButton>
                      {!locked && <NavBadge item={item} />}
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      </nav>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild isActive={isActive(pathname, SETTINGS_NAV.to)} tooltip="Settings">
              <NavLink to={SETTINGS_NAV.to} onClick={() => setOpenMobile(false)}>
                <Settings />
                <span>Settings</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <UserMenu authEnabled={authEnabled} />
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function UserMenu({ authEnabled }: { authEnabled: boolean }) {
  const { identity, account, cloud, signOut } = useAuth()
  const { theme, setTheme } = useTheme()
  const { isMobile } = useSidebar()
  const name = account ? account.name || account.email : identity ? (identity.key_id ?? "API key") : "Open mode"
  const sub = account
    ? `${account.workspace.name} · ${account.cloud_role}`
    : identity
      ? [identity.org_id, identity.team_id].filter(Boolean).join(" / ") + (identity.role ? ` · ${identity.role}` : "")
      : authEnabled
        ? "Not signed in"
        : "Gateway auth disabled"
  const initials = account
    ? (account.name || account.email)
        .split(/[\s@.]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0]!.toUpperCase())
        .join("")
    : identity
      ? identity.org_id.slice(0, 2).toUpperCase()
      : "—"
  const signedIn = !!(account || identity)
  const nameClass = account ? "truncate font-medium" : "truncate font-mono text-[12px] font-medium"

  return (
    <SidebarMenuItem>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
            <Avatar className="size-8 rounded-lg">
              <AvatarFallback className="rounded-lg bg-primary text-[11px] font-semibold text-primary-foreground">
                {signedIn ? initials : <UserRound className="size-4" />}
              </AvatarFallback>
            </Avatar>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className={nameClass}>{name}</span>
              <span className="truncate text-[11px] text-sidebar-foreground/75">{sub}</span>
            </div>
            <ChevronsUpDown className="ml-auto size-4 opacity-60" />
          </SidebarMenuButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="w-(--radix-dropdown-menu-trigger-width) min-w-60 rounded-lg"
          side={isMobile ? "bottom" : "right"}
          align="end"
          sideOffset={4}
        >
          <DropdownMenuLabel className="font-normal">
            <div className="space-y-0.5">
              <div className={account ? "text-sm font-medium" : "font-mono text-xs font-medium"}>{name}</div>
              {account && account.name && <div className="text-xs text-muted-foreground">{account.email}</div>}
              <div className="text-xs text-muted-foreground">{sub}</div>
              {account && (
                <div className="text-xs text-muted-foreground">
                  On this gateway: <span className="font-medium text-foreground">{account.role}</span>
                </div>
              )}
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Sun />
                Theme
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
                  <DropdownMenuRadioItem value="light">
                    <Sun /> Light
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="dark">
                    <Moon /> Dark
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="system">
                    <Monitor /> System
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem asChild>
              <Link to="/settings">
                <Settings />
                Settings
              </Link>
            </DropdownMenuItem>
            {cloud && (
              <DropdownMenuItem asChild>
                <a href={`${cloud.url}/ui/`} target="_blank" rel="noopener noreferrer">
                  <Cloud />
                  Open Meridian Cloud
                  <ExternalLink className="ml-auto size-3 opacity-60" />
                </a>
              </DropdownMenuItem>
            )}
          </DropdownMenuGroup>
          {(authEnabled || account) && (
            <>
              <DropdownMenuSeparator />
              {signedIn ? (
                <DropdownMenuItem variant="destructive" onClick={signOut}>
                  <LogOut />
                  Sign out
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem onClick={signOut}>
                  <KeyRound />
                  Sign in
                </DropdownMenuItem>
              )}
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}
