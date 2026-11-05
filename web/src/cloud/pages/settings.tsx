import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, LogOut, Mail, MonitorSmartphone, Plus, Trash2, UserPlus } from "lucide-react"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"

import { ConfirmDialog, TimeAgo, Timestamp } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { ErrorState, LoadingRows } from "@/components/states"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError } from "@/lib/api/client"
import { useSettings } from "@/providers/settings-provider"

import { atLeast, cloud, type Member, type WorkspaceRole } from "../api"
import { useGatewayTokens, useMembers } from "../hooks"
import { useCloud, useWorkspace } from "../session"

const ROLE_HELP: Record<WorkspaceRole, string> = {
  viewer: "Read everything in the workspace",
  operator: "Viewer + enroll nodes, approve claims, publish desired state",
  admin: "Operator + revoke nodes, gateway tokens, invite members",
  owner: "Admin + manage owners",
}

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "Something went wrong"
}

function initials(m: { name: string; email: string }) {
  return (m.name || m.email).split(/[\s@.]+/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("")
}

function WorkspaceTab() {
  const ws = useWorkspace()
  const { refresh } = useCloud()
  const { settings, update } = useSettings()
  const [name, setName] = useState(ws.name)
  const rename = useMutation({
    mutationFn: () => cloud.renameWorkspace(ws.id, name.trim()),
    onSuccess: async () => {
      await refresh()
      toast.success("Workspace renamed")
    },
  })
  const canAdmin = atLeast(ws.role, "admin")
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="Workspace">
        <div className="space-y-5">
          <Field data-invalid={rename.isError}>
            <FieldLabel htmlFor="ws-rename">Name</FieldLabel>
            <div className="flex gap-2">
              <Input id="ws-rename" value={name} onChange={(e) => setName(e.target.value)} disabled={!canAdmin} maxLength={120} />
              {canAdmin && (
                <Button onClick={() => rename.mutate()} disabled={rename.isPending || name.trim() === ws.name || name.trim().length < 2}>
                  {rename.isPending && <Spinner data-icon="inline-start" />}
                  Save
                </Button>
              )}
            </div>
            {rename.isError && <FieldError>{errMsg(rename.error)}</FieldError>}
          </Field>
          <KeyValueGrid
            items={[
              { label: "Workspace ID", value: ws.id, mono: true },
              { label: "Slug", value: ws.slug, mono: true },
              { label: "Your role", value: ws.role },
            ]}
          />
        </div>
      </Panel>
      <Panel title="Preferences" description="Stored in this browser">
        <Field>
          <FieldLabel htmlFor="cloud-refresh">Live refresh interval</FieldLabel>
          <Select value={String(settings.refreshMs)} onValueChange={(v) => update({ refreshMs: Number(v) })}>
            <SelectTrigger id="cloud-refresh" className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[2000, 5000, 10000, 30000].map((ms) => (
                <SelectItem key={ms} value={String(ms)}>
                  Every {ms / 1000}s
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>Gateways report every 15 seconds, so faster refresh mostly helps fleet pages.</FieldDescription>
        </Field>
      </Panel>
    </div>
  )
}

function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const ws = useWorkspace()
  const qc = useQueryClient()
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<WorkspaceRole>("viewer")
  const invite = useMutation({
    mutationFn: () => cloud.invite(ws.id, email.trim(), role),
    onSuccess: (inv) => {
      toast.success(`Invitation sent to ${inv.email}`, { description: "They join as soon as they sign in with that address." })
      void qc.invalidateQueries({ queryKey: ["cloud", "members", ws.id] })
      setEmail("")
      onOpenChange(false)
    },
  })
  const roles: WorkspaceRole[] = ws.role === "owner" ? ["viewer", "operator", "admin"] : (["viewer", "operator", "admin"] as WorkspaceRole[]).filter((r) => atLeast(ws.role, r))
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite to {ws.name}</DialogTitle>
          <DialogDescription>They sign in with Google or an email code using this address; the workspace appears automatically.</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            invite.mutate()
          }}
        >
          <Field data-invalid={invite.isError}>
            <FieldLabel htmlFor="inv-email">Email</FieldLabel>
            <Input id="inv-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="teammate@company.com" autoFocus />
            {invite.isError && <FieldError>{errMsg(invite.error)}</FieldError>}
          </Field>
          <Field>
            <FieldLabel htmlFor="inv-role">Role</FieldLabel>
            <Select value={role} onValueChange={(v) => setRole(v as WorkspaceRole)}>
              <SelectTrigger id="inv-role" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {roles.map((r) => (
                  <SelectItem key={r} value={r}>
                    <span className="capitalize">{r}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{ROLE_HELP[r]}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => invite.mutate()} disabled={invite.isPending || !email.includes("@")}>
            {invite.isPending ? <Spinner data-icon="inline-start" /> : <Mail data-icon="inline-start" />}
            Send invitation
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MembersTab() {
  const ws = useWorkspace()
  const { me, refresh } = useCloud()
  const qc = useQueryClient()
  const members = useMembers()
  const [inviting, setInviting] = useState(false)
  const [removing, setRemoving] = useState<Member | null>(null)
  const canAdmin = atLeast(ws.role, "admin")
  const invalidate = () => qc.invalidateQueries({ queryKey: ["cloud", "members", ws.id] })
  const setRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: WorkspaceRole }) => cloud.setRole(ws.id, id, role),
    onSuccess: () => {
      toast.success("Role updated")
      void invalidate()
      void refresh()
    },
    onError: (e) => toast.error("Couldn't change role", { description: errMsg(e) }),
  })
  const remove = useMutation({
    mutationFn: (id: string) => cloud.removeMember(ws.id, id),
    onSuccess: async (_r, id) => {
      setRemoving(null)
      if (id === me?.user.id) {
        toast.success(`You left ${ws.name}`)
        await refresh()
      } else {
        toast.success("Member removed")
        void invalidate()
      }
    },
    onError: (e) => toast.error("Couldn't remove", { description: errMsg(e) }),
  })
  const revokeInvite = useMutation({
    mutationFn: (id: string) => cloud.revokeInvite(ws.id, id),
    onSuccess: () => {
      toast.success("Invitation revoked")
      void invalidate()
    },
  })

  if (members.isError) return <ErrorState error={members.error} onRetry={() => members.refetch()} />
  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Members"
        description="Roles: viewer → operator → admin → owner. Each includes everything to its left."
        actions={
          canAdmin && (
            <Button size="sm" onClick={() => setInviting(true)}>
              <UserPlus data-icon="inline-start" /> Invite
            </Button>
          )
        }
        contentClassName="px-0"
      >
        {!members.data ? (
          <LoadingRows rows={3} className="px-4" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4 text-xs">Member</TableHead>
                <TableHead className="text-xs">Role</TableHead>
                <TableHead className="text-xs">Joined</TableHead>
                <TableHead className="text-xs">Last sign-in</TableHead>
                <TableHead className="pr-4 text-right text-xs" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data.members.map((m) => {
                const self = m.id === me?.user.id
                const editable = canAdmin && !self && (m.role !== "owner" || ws.role === "owner")
                return (
                  <TableRow key={m.id}>
                    <TableCell className="pl-4">
                      <div className="flex items-center gap-3">
                        <Avatar className="size-8">
                          {m.avatar_url && <AvatarImage src={m.avatar_url} alt="" referrerPolicy="no-referrer" />}
                          <AvatarFallback className="text-[11px]">{initials(m)}</AvatarFallback>
                        </Avatar>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 truncate text-sm font-medium">
                            {m.name || m.email}
                            {self && (
                              <Badge variant="secondary" className="h-5 text-[10px]">
                                you
                              </Badge>
                            )}
                          </div>
                          <div className="truncate text-xs text-muted-foreground">{m.email}</div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      {editable ? (
                        <Select value={m.role} onValueChange={(r) => setRole.mutate({ id: m.id, role: r as WorkspaceRole })}>
                          <SelectTrigger size="sm" className="w-32 capitalize">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(["viewer", "operator", "admin", ...(ws.role === "owner" ? ["owner"] : [])] as WorkspaceRole[]).map((r) => (
                              <SelectItem key={r} value={r} className="capitalize">
                                {r}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <span className="text-sm capitalize">{m.role}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs">
                      <Timestamp value={m.joined_at} seconds={false} />
                    </TableCell>
                    <TableCell className="text-xs">
                      <TimeAgo value={m.last_login_at} />
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      {(editable || self) && (
                        <Button variant="ghost" size="sm" onClick={() => setRemoving(m)}>
                          {self ? "Leave" : "Remove"}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Panel>
      {!!members.data?.invitations.length && (
        <Panel title="Pending invitations" contentClassName="px-0">
          <Table>
            <TableBody>
              {members.data.invitations.map((i) => (
                <TableRow key={i.id}>
                  <TableCell className="pl-4 text-sm">{i.email}</TableCell>
                  <TableCell className="text-sm capitalize">{i.role}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    expires <TimeAgo value={i.expires_at} />
                  </TableCell>
                  <TableCell className="pr-4 text-right">
                    {canAdmin && (
                      <Button variant="ghost" size="sm" onClick={() => revokeInvite.mutate(i.id)}>
                        Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      )}
      <InviteDialog open={inviting} onOpenChange={setInviting} />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={removing?.id === me?.user.id ? `Leave ${ws.name}?` : `Remove ${removing?.email}?`}
        description={<p>{removing?.id === me?.user.id ? "You lose access until someone invites you again." : "They lose access immediately. Their audit history is kept."}</p>}
        confirmLabel={removing?.id === me?.user.id ? "Leave workspace" : "Remove member"}
        destructive
        pending={remove.isPending}
        onConfirm={() => {
          if (removing) remove.mutate(removing.id)
        }}
      />
    </div>
  )
}

function TokensTab() {
  const ws = useWorkspace()
  const qc = useQueryClient()
  const tokens = useGatewayTokens(atLeast(ws.role, "admin"))
  const [revoking, setRevoking] = useState<{ id: string; name: string } | null>(null)
  const revoke = useMutation({
    mutationFn: (id: string) => cloud.revokeGatewayToken(ws.id, id),
    onSuccess: () => {
      toast.success("Token revoked", { description: "Gateways using it lose access on their next call." })
      setRevoking(null)
      void qc.invalidateQueries({ queryKey: ["cloud", "gateway-tokens", ws.id] })
    },
  })
  if (!atLeast(ws.role, "admin")) return <ErrorState error={new ApiError("Gateway tokens are managed by admins", { status: 403 })} />
  return (
    <Panel
      title="Gateway tokens"
      description="Gateways authenticate with these to pull their engines and send reports. Only a prefix is stored for display."
      actions={
        <Button size="sm" asChild>
          <Link to="/gateways?connect=1">
            <Plus data-icon="inline-start" /> New token
          </Link>
        </Button>
      }
      contentClassName="px-0"
    >
      {!tokens.data ? (
        <LoadingRows rows={3} className="px-4" />
      ) : tokens.data.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">No tokens yet. Connect a gateway to create one.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-4 text-xs">Name</TableHead>
              <TableHead className="text-xs">Token</TableHead>
              <TableHead className="text-xs">Created</TableHead>
              <TableHead className="text-xs">Last used</TableHead>
              <TableHead className="text-xs">Status</TableHead>
              <TableHead className="pr-4" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {tokens.data.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="pl-4 font-medium">{t.name}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{t.prefix}…</TableCell>
                <TableCell className="text-xs">
                  <Timestamp value={t.created_at} seconds={false} />
                </TableCell>
                <TableCell className="text-xs">
                  <TimeAgo value={t.last_used_at} />
                </TableCell>
                <TableCell>
                  <StatusBadge tone={t.revoked ? "muted" : "good"} size="sm">
                    {t.revoked ? "Revoked" : "Active"}
                  </StatusBadge>
                </TableCell>
                <TableCell className="pr-4 text-right">
                  {!t.revoked && (
                    <Button variant="ghost" size="sm" onClick={() => setRevoking({ id: t.id, name: t.name })}>
                      <Trash2 data-icon="inline-start" /> Revoke
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke ${revoking?.name}?`}
        description={<p>Gateways using this token stop receiving managed engines and stop reporting. Their own traffic keeps flowing.</p>}
        confirmLabel="Revoke token"
        destructive
        typeToConfirm={revoking?.name}
        pending={revoke.isPending}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking.id)
        }}
      />
    </Panel>
  )
}

function AccountTab() {
  const { me, refresh, signOut } = useCloud()
  const qc = useQueryClient()
  const [name, setName] = useState(me?.user.name ?? "")
  const sessions = useQuery({ queryKey: ["cloud", "sessions"], queryFn: ({ signal }) => cloud.sessions(signal) })
  const save = useMutation({
    mutationFn: () => cloud.updateMe(name.trim()),
    onSuccess: async () => {
      await refresh()
      toast.success("Profile updated")
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => cloud.revokeSession(id),
    onSuccess: () => {
      toast.success("Session signed out")
      void qc.invalidateQueries({ queryKey: ["cloud", "sessions"] })
    },
  })
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
      <Panel title="Profile">
        <div className="space-y-4">
          <Field data-invalid={save.isError}>
            <FieldLabel htmlFor="me-name">Display name</FieldLabel>
            <div className="flex gap-2">
              <Input id="me-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
              <Button onClick={() => save.mutate()} disabled={save.isPending || !name.trim() || name.trim() === me?.user.name}>
                Save
              </Button>
            </div>
            {save.isError && <FieldError>{errMsg(save.error)}</FieldError>}
          </Field>
          <KeyValueGrid
            items={[
              { label: "Email", value: me?.user.email ?? "—" },
              { label: "Member since", value: <Timestamp value={me?.user.created_at} seconds={false} /> },
            ]}
          />
          <Button variant="outline" onClick={() => void signOut()}>
            <LogOut data-icon="inline-start" /> Sign out
          </Button>
        </div>
      </Panel>
      <Panel title="Active sessions" description="Browsers signed in to your account" contentClassName="px-0">
        {!sessions.data ? (
          <LoadingRows rows={2} className="px-4" />
        ) : (
          <ul className="divide-y">
            {sessions.data.map((s) => (
              <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                <MonitorSmartphone className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{s.user_agent || "Unknown browser"}</div>
                  <div className="text-xs text-muted-foreground">
                    {s.method === "google" ? "Google" : "Email code"} · {s.ip || "—"} · active <TimeAgo value={s.last_seen_at} />
                  </div>
                </div>
                {s.current ? (
                  <StatusBadge tone="good" size="sm" icon={KeyRound}>
                    This browser
                  </StatusBadge>
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => revoke.mutate(s.id)}>
                    Sign out
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}

export default function CloudSettingsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") ?? "workspace"
  return (
    <Page>
      <PageHeader title="Settings" description="Workspace, team, gateway credentials and your account." />
      <Tabs value={tab} onValueChange={(t) => setParams(t === "workspace" ? {} : { tab: t }, { replace: true })} className="gap-4">
        <TabsList>
          <TabsTrigger value="workspace">Workspace</TabsTrigger>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="tokens">Gateway tokens</TabsTrigger>
          <TabsTrigger value="account">Account</TabsTrigger>
        </TabsList>
        <TabsContent value="workspace">
          <WorkspaceTab />
        </TabsContent>
        <TabsContent value="members">
          <MembersTab />
        </TabsContent>
        <TabsContent value="tokens">
          <TokensTab />
        </TabsContent>
        <TabsContent value="account">
          <AccountTab />
        </TabsContent>
      </Tabs>
    </Page>
  )
}
