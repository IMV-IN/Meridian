import { Cpu, KeyRound, LogOut, Moon, Pause, Play, RefreshCw, ScrollText, Server, Sun, Ticket } from "lucide-react"
import { useTheme } from "@/providers/theme-provider"
import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command"
import { useNodes, useStatus } from "@/hooks/queries"
import { useAuth } from "@/providers/auth-provider"
import { useSettings } from "@/providers/settings-provider"

import { ALL_NAV } from "./nav"

export function useCommandMenu() {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setOpen((o) => !o)
      }
      // "/" opens search unless the user is typing somewhere.
      const t = e.target as HTMLElement | null
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])
  return { open, setOpen }
}

export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate()
  const { can, authEnabled, identity, signOut } = useAuth()
  const { settings, update } = useSettings()
  const { resolvedTheme, setTheme } = useTheme()
  const status = useStatus()
  const nodes = useNodes()
  const [query, setQuery] = useState("")
  const handleOpenChange = (o: boolean) => {
    if (!o) setQuery("")
    onOpenChange(o)
  }

  const run = (fn: () => void) => {
    handleOpenChange(false)
    fn()
  }

  const pages = ALL_NAV.filter((n) => !n.need || can(n.need))
  const looksLikeRequestId = /^(mrdn-)?[0-9a-f]{8,}$/i.test(query.trim())

  return (
    <CommandDialog open={open} onOpenChange={handleOpenChange} title="Search the console" description="Jump to a page, backend, node or action">
      <Command loop>
        <CommandInput placeholder="Search pages, backends, nodes, actions…" value={query} onValueChange={setQuery} />
        <CommandList className="max-h-[420px]">
          <CommandEmpty>No matches.</CommandEmpty>
          {looksLikeRequestId && can("view_ops") && (
            <CommandGroup heading="Request">
              <CommandItem
                value={`request ${query}`}
                onSelect={() => run(() => navigate(`/requests?q=${encodeURIComponent(query.trim())}`))}
              >
                <ScrollText />
                Find request <span className="font-mono">{query.trim()}</span>
              </CommandItem>
            </CommandGroup>
          )}
          <CommandGroup heading="Pages">
            {pages.map((p) => (
              <CommandItem
                key={p.to}
                value={`${p.title} ${p.keywords?.join(" ") ?? ""}`}
                onSelect={() => run(() => navigate(p.to))}
              >
                <p.icon />
                <span>{p.title}</span>
                <span className="ml-2 truncate text-xs text-muted-foreground">{p.description}</span>
              </CommandItem>
            ))}
          </CommandGroup>
          {status.data && status.data.backends.length > 0 && (
            <CommandGroup heading="Backends">
              {status.data.backends.map((b) => (
                <CommandItem
                  key={b.name}
                  value={`backend ${b.name} ${b.model} ${b.engine} ${b.tags.join(" ")}`}
                  onSelect={() => run(() => navigate(`/backends/${encodeURIComponent(b.name)}`))}
                >
                  <Server />
                  <span>{b.name}</span>
                  <span className="ml-2 truncate text-xs text-muted-foreground">
                    {b.engine} · {b.model || "any model"}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          {nodes.data && nodes.data.length > 0 && (
            <CommandGroup heading="Nodes">
              {nodes.data.slice(0, 30).map((n) => (
                <CommandItem
                  key={n.node_id}
                  value={`node ${n.node_id} ${n.display_name}`}
                  onSelect={() => run(() => navigate(`/fleet/nodes/${encodeURIComponent(n.node_id)}`))}
                >
                  <Cpu />
                  <span>{n.display_name || n.node_id}</span>
                  <span className="ml-2 truncate font-mono text-xs text-muted-foreground">{n.node_id}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
          <CommandSeparator />
          <CommandGroup heading="Actions">
            {can("manage_keys") && (
              <CommandItem value="create api key new" onSelect={() => run(() => navigate("/keys?create=1"))}>
                <KeyRound />
                Create API key
              </CommandItem>
            )}
            {can("view_ops") && (
              <CommandItem value="mint enrollment token join node" onSelect={() => run(() => navigate("/fleet/enrollment?mint=1"))}>
                <Ticket />
                Mint enrollment token
              </CommandItem>
            )}
            {can("reload") && (
              <CommandItem value="reload gateway config hot" onSelect={() => run(() => navigate("/settings?tab=gateway"))}>
                <RefreshCw />
                Reload gateway configuration…
              </CommandItem>
            )}
            <CommandItem
              value="toggle theme dark light"
              onSelect={() => run(() => setTheme(resolvedTheme === "dark" ? "light" : "dark"))}
            >
              {resolvedTheme === "dark" ? <Sun /> : <Moon />}
              Switch to {resolvedTheme === "dark" ? "light" : "dark"} theme
            </CommandItem>
            {can("view_ops") && (
              <CommandItem value="pause resume live updates" onSelect={() => run(() => update({ live: !settings.live }))}>
                {settings.live ? <Pause /> : <Play />}
                {settings.live ? "Pause live updates" : "Resume live updates"}
              </CommandItem>
            )}
            {authEnabled && identity && (
              <CommandItem value="sign out logout" onSelect={() => run(signOut)}>
                <LogOut />
                Sign out
                <CommandShortcut>{identity.key_id}</CommandShortcut>
              </CommandItem>
            )}
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  )
}
