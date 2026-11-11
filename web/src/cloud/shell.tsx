import { useMutation } from "@tanstack/react-query"
import {
  Building2,
  Check,
  ChevronsUpDown,
  Cpu,
  FileClock,
  LayoutDashboard,
  LogOut,
  Monitor,
  Moon,
  Network,
  Plus,
  Settings,
  SquareTerminal,
  Store,
  Sun,
  Ticket,
  UserRound,
  Wallet,
  Waypoints,
  type LucideIcon,
} from "lucide-react"
import { Suspense, useEffect, useState } from "react"
import { Link, NavLink, Outlet, useLocation, useMatches, useNavigate, type UIMatch } from "react-router"
import { toast } from "sonner"

import { MeridianMark } from "@/components/layout/logo"
import { SiteHeader, type CrumbHandle } from "@/components/layout/site-header"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
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
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useClaims, useNodes } from "@/hooks/queries"
import { ApiError } from "@/lib/api/client"
import { BootScreen } from "@/pages/sign-in"
import { useTheme } from "@/providers/theme-provider"
import { autoFocusDesktop } from "@/lib/utils"

import { cloud } from "./api"
import { CloudSignIn } from "./pages/sign-in"
import { useCloud } from "./session"

interface CloudNav {
  title: string
  to: string
  icon: LucideIcon
}

const NAV: { label: string; items: CloudNav[] }[] = [
  {
    label: "Workspace",
    items: [
      { title: "Overview", to: "/", icon: LayoutDashboard },
      { title: "Gateways", to: "/gateways", icon: Network },
    ],
  },
  {
    label: "Compute",
    items: [
      { title: "Marketplace", to: "/marketplace", icon: Store },
      { title: "Jobs", to: "/jobs", icon: SquareTerminal },
      { title: "Billing", to: "/billing", icon: Wallet },
    ],
  },
  {
    label: "GPU fleet",
    items: [
      { title: "Nodes", to: "/fleet", icon: Cpu },
      { title: "Enrollment", to: "/fleet/enrollment", icon: Ticket },
      { title: "Serving & placement", to: "/fleet/serving", icon: Waypoints },
      { title: "Audit log", to: "/fleet/audit", icon: FileClock },
    ],
  },
]

function isActive(pathname: string, to: string) {
  if (to === "/") return pathname === "/"
  if (to === "/fleet") return pathname === "/fleet" || pathname.startsWith("/fleet/nodes")
  return pathname === to || pathname.startsWith(`${to}/`)
}

export function CreateWorkspaceDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { setWorkspace, refresh } = useCloud()
  const [name, setName] = useState("")
  const create = useMutation({
    mutationFn: () => cloud.createWorkspace(name.trim()),
    onSuccess: async (ws) => {
      await refresh()
      setWorkspace(ws.id)
      onOpenChange(false)
      setName("")
      toast.success(`Workspace ${ws.name} created`)
    },
  })
  const err = create.error instanceof ApiError ? create.error.message : null
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create a workspace</DialogTitle>
          <DialogDescription>A workspace holds your gateways, GPU nodes and team. You become its owner.</DialogDescription>
        </DialogHeader>
        <WorkspaceNameForm name={name} setName={setName} err={err} pending={create.isPending} onSubmit={() => create.mutate()} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => create.mutate()} disabled={create.isPending || name.trim().length < 2}>
            {create.isPending && <Spinner data-icon="inline-start" />}
            Create workspace
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WorkspaceNameForm({
  name,
  setName,
  err,
  pending,
  onSubmit,
}: {
  name: string
  setName: (v: string) => void
  err: string | null
  pending: boolean
  onSubmit: () => void
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (name.trim().length >= 2 && !pending) onSubmit()
      }}
    >
      <Field data-invalid={!!err}>
        <FieldLabel htmlFor="ws-name">Workspace name</FieldLabel>
        <Input id="ws-name" name="organization" autoComplete="organization" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Acme AI platform" autoFocus={autoFocusDesktop()} maxLength={120} />
        {err ? <FieldError>{err}</FieldError> : <FieldDescription>Usually your company or team. You can rename it later.</FieldDescription>}
      </Field>
    </form>
  )
}

function Onboarding() {
  const { me, refresh, setWorkspace, signOut } = useCloud()
  const [name, setName] = useState("")
  const create = useMutation({
    mutationFn: () => cloud.createWorkspace(name.trim()),
    onSuccess: async (ws) => {
      setWorkspace(ws.id)
      await refresh()
    },
  })
  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-6">
      <div className="w-full max-w-md space-y-8">
        <MeridianMark className="size-10" />
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Welcome{me?.user.name ? `, ${me.user.name.split(" ")[0]}` : ""}</h1>
          <p className="text-sm text-muted-foreground">
            Create a workspace to connect your gateways and GPU nodes. If a teammate invited you, ask them to use{" "}
            <span className="font-medium text-foreground">{me?.user.email}</span> — the workspace appears here automatically.
          </p>
        </div>
        <div className="space-y-4">
          <WorkspaceNameForm
            name={name}
            setName={setName}
            err={create.error instanceof ApiError ? create.error.message : null}
            pending={create.isPending}
            onSubmit={() => create.mutate()}
          />
          <Button className="h-10 w-full" onClick={() => create.mutate()} disabled={create.isPending || name.trim().length < 2}>
            {create.isPending && <Spinner data-icon="inline-start" />}
            Create workspace
          </Button>
        </div>
        <button type="button" className="text-xs text-muted-foreground underline underline-offset-2" onClick={() => void signOut()}>
          Sign out ({me?.user.email})
        </button>
      </div>
    </main>
  )
}

function WorkspaceSwitcher() {
  const { me, workspace, setWorkspace } = useCloud()
  const [creating, setCreating] = useState(false)
  const { isMobile } = useSidebar()
  if (!workspace) return null
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
              <MeridianMark className="size-8" />
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate text-sm font-semibold">{workspace.name}</span>
                <span className="truncate text-[11px] text-sidebar-foreground/75">Meridian Cloud · {workspace.role}</span>
              </div>
              <ChevronsUpDown className="ml-auto size-4 opacity-60" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-(--radix-dropdown-menu-trigger-width) min-w-60" align="start" side={isMobile ? "bottom" : "right"} sideOffset={4}>
            <DropdownMenuLabel className="text-xs text-muted-foreground">Workspaces</DropdownMenuLabel>
            {me?.workspaces.map((w) => (
              <DropdownMenuItem key={w.id} onClick={() => setWorkspace(w.id)}>
                <Building2 />
                <span className="flex-1 truncate">{w.name}</span>
                {w.id === workspace.id && <Check className="size-4" />}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => setCreating(true)}>
              <Plus /> Create workspace
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <CreateWorkspaceDialog open={creating} onOpenChange={setCreating} />
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

function UserMenu() {
  const { me, signOut } = useCloud()
  const { theme, setTheme } = useTheme()
  const { isMobile } = useSidebar()
  const navigate = useNavigate()
  const user = me?.user
  if (!user) return null
  const initials = (user.name || user.email).split(/[\s@.]+/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("")
  return (
    <SidebarMenuItem>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
            <Avatar className="size-8 rounded-lg">
              {user.avatar_url && <AvatarImage src={user.avatar_url} alt="" referrerPolicy="no-referrer" />}
              <AvatarFallback className="rounded-lg bg-primary text-[11px] font-semibold text-primary-foreground">{initials || <UserRound className="size-4" />}</AvatarFallback>
            </Avatar>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">{user.name || user.email}</span>
              <span className="truncate text-[11px] text-sidebar-foreground/75">{user.email}</span>
            </div>
            <ChevronsUpDown className="ml-auto size-4 opacity-60" />
          </SidebarMenuButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-(--radix-dropdown-menu-trigger-width) min-w-56" side={isMobile ? "bottom" : "right"} align="end" sideOffset={4}>
          <DropdownMenuLabel className="font-normal">
            <div className="text-sm font-medium">{user.name}</div>
            <div className="text-xs text-muted-foreground">{user.email}</div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate("/settings?tab=account")}>
            <UserRound /> Account & sessions
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Sun /> Theme
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
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => void signOut()}>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}

function PendingBadge({ to }: { to: string }) {
  const pending = useClaims("pending")
  if (to !== "/fleet/enrollment" || !pending.data?.length) return null
  return <SidebarMenuBadge className="bg-[color-mix(in_oklab,var(--status-warning)_22%,transparent)] text-foreground">{pending.data.length}</SidebarMenuBadge>
}

function CloudSidebar() {
  const { pathname } = useLocation()
  const { setOpenMobile } = useSidebar()
  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <WorkspaceSwitcher />
      </SidebarHeader>
      <nav aria-label="Primary" className="flex min-h-0 flex-1 flex-col">
        <SidebarContent className="scrollbar-thin">
        {NAV.map((g) => (
          <SidebarGroup key={g.label}>
            <SidebarGroupLabel>{g.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {g.items.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton asChild isActive={isActive(pathname, item.to)} tooltip={item.title}>
                      <NavLink to={item.to} end={item.to === "/"} onClick={() => setOpenMobile(false)}>
                        <item.icon />
                        <span>{item.title}</span>
                      </NavLink>
                    </SidebarMenuButton>
                    <PendingBadge to={item.to} />
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      </nav>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild isActive={isActive(pathname, "/settings")} tooltip="Settings">
              <NavLink to="/settings" onClick={() => setOpenMobile(false)}>
                <Settings />
                <span>Settings</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <UserMenu />
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function CloudCommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate()
  const nodes = useNodes()
  const { me, setWorkspace, workspace } = useCloud()
  const go = (to: string) => {
    onOpenChange(false)
    navigate(to)
  }
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Search" description="Jump to a page, node or workspace">
      <Command loop>
        <CommandInput placeholder="Search pages, nodes, workspaces…" />
        <CommandList>
          <CommandEmpty>No matches.</CommandEmpty>
          <CommandGroup heading="Pages">
            {[...NAV.flatMap((g) => g.items), { title: "Settings", to: "/settings", icon: Settings }].map((p) => (
              <CommandItem key={p.to} value={p.title} onSelect={() => go(p.to)}>
                <p.icon /> {p.title}
              </CommandItem>
            ))}
          </CommandGroup>
          {!!nodes.data?.length && (
            <CommandGroup heading="Nodes">
              {nodes.data.slice(0, 30).map((n) => (
                <CommandItem key={n.node_id} value={`node ${n.display_name} ${n.node_id}`} onSelect={() => go(`/fleet/nodes/${encodeURIComponent(n.node_id)}`)}>
                  <Cpu /> {n.display_name || n.node_id}
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {(me?.workspaces.length ?? 0) > 1 && (
            <CommandGroup heading="Switch workspace">
              {me!.workspaces
                .filter((w) => w.id !== workspace?.id)
                .map((w) => (
                  <CommandItem
                    key={w.id}
                    value={`workspace ${w.name}`}
                    onSelect={() => {
                      onOpenChange(false)
                      setWorkspace(w.id)
                      navigate("/")
                    }}
                  >
                    <Building2 /> {w.name}
                  </CommandItem>
                ))}
            </CommandGroup>
          )}
        </CommandList>
      </Command>
    </CommandDialog>
  )
}

function useDocumentTitle() {
  const matches = useMatches() as UIMatch<unknown, CrumbHandle>[]
  const { workspace } = useCloud()
  const last = [...matches].reverse().find((m) => m.handle?.crumb)
  const label = last?.handle.crumb?.(last.params)
  useEffect(() => {
    document.title = [label && label !== "Console" ? label : null, workspace?.name, "Meridian"].filter(Boolean).join(" · ")
  }, [label, workspace?.name])
}

function PageFallback() {
  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-6 p-4 md:p-6 lg:px-8">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-80 rounded-xl" />
    </div>
  )
}

function SignedInShell() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  const { workspace } = useCloud()
  useDocumentTitle()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  return (
    <SidebarProvider>
      <CloudSidebar />
      <SidebarInset className="min-w-0">
        <SiteHeader onOpenCommand={() => setOpen(true)} />
        <main id="main" className="flex-1">
          <Suspense key={`${workspace?.id}:${pathname}`} fallback={<PageFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </SidebarInset>
      <CloudCommandMenu open={open} onOpenChange={setOpen} />
    </SidebarProvider>
  )
}

export function CloudShell() {
  const { phase, error, refresh } = useCloud()
  if (phase === "loading") return <BootScreen />
  if (phase === "signed-out") return <CloudSignIn />
  if (phase === "onboarding") return <Onboarding />
  if (phase === "unreachable")
    return (
      <main className="flex min-h-svh flex-col items-center justify-center gap-4 p-6 text-center">
        <MeridianMark className="size-10" />
        <h1 className="text-base font-semibold">Can't reach Meridian Cloud</h1>
        <p className="max-w-sm text-sm text-muted-foreground">{error?.message ?? "Check your connection, then try again."}</p>
        <Button variant="outline" onClick={() => void refresh()}>
          Try again
        </Button>
        <Link to="/" className="sr-only">
          home
        </Link>
      </main>
    )
  return <SignedInShell />
}
