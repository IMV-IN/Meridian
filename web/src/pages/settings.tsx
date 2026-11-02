import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CircleCheck, KeyRound, LogOut, Monitor, Moon, PlugZap, RefreshCw, ShieldAlert, Sun, Unplug } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { CodeBlock, JsonView } from "@/components/code"
import { ConfirmDialog } from "@/components/misc"
import { KeyValueGrid, Page, PageHeader, Panel } from "@/components/page"
import { StatusBadge } from "@/components/status-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { useConfig, useVersion } from "@/hooks/queries"
import { ApiError } from "@/lib/api/client"
import { controlApi } from "@/lib/api/control"
import { credentials, maskKey } from "@/lib/api/credentials"
import { gateway, gatewayOrigin } from "@/lib/api/gateway"
import type { ReloadReport } from "@/lib/api/types"
import { titleCase } from "@/lib/format"
import { useAuth } from "@/providers/auth-provider"
import { useSettings } from "@/providers/settings-provider"
import { useTheme } from "@/providers/theme-provider"

function GeneralTab() {
  const { settings, update, reset } = useSettings()
  const { theme, setTheme } = useTheme()
  return (
    <Panel title="Preferences" description="Stored in this browser only.">
      <FieldGroup className="max-w-xl gap-6">
        <Field>
          <FieldLabel>Theme</FieldLabel>
          <ToggleGroup type="single" variant="outline" value={theme} onValueChange={(v) => v && setTheme(v)} className="justify-start">
            <ToggleGroupItem value="light" className="px-3">
              <Sun /> Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark" className="px-3">
              <Moon /> Dark
            </ToggleGroupItem>
            <ToggleGroupItem value="system" className="px-3">
              <Monitor /> System
            </ToggleGroupItem>
          </ToggleGroup>
        </Field>
        <Field>
          <FieldLabel htmlFor="s-refresh">Live refresh interval</FieldLabel>
          <Select value={String(settings.refreshMs)} onValueChange={(v) => update({ refreshMs: Number(v) })}>
            <SelectTrigger id="s-refresh" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[1000, 2000, 5000, 10000, 30000].map((ms) => (
                <SelectItem key={ms} value={String(ms)}>
                  Every {ms / 1000}s
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>Faster refresh gives smoother live charts at the cost of more requests to the gateway. Polling pauses in background tabs.</FieldDescription>
        </Field>
        <Field>
          <FieldLabel htmlFor="s-tz">Timestamps</FieldLabel>
          <Select value={settings.timeZone} onValueChange={(v) => update({ timeZone: v as "local" | "utc" })}>
            <SelectTrigger id="s-tz" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="local">Local time ({Intl.DateTimeFormat().resolvedOptions().timeZone})</SelectItem>
              <SelectItem value="utc">UTC</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field orientation="horizontal" className="items-start justify-between">
          <div>
            <FieldLabel htmlFor="s-toasts">Alert notifications</FieldLabel>
            <FieldDescription>Pop a toast when a backend goes down, a circuit opens or a canary rolls back.</FieldDescription>
          </div>
          <Switch id="s-toasts" checked={settings.alertToasts} onCheckedChange={(c) => update({ alertToasts: c })} />
        </Field>
        <div>
          <Button variant="outline" size="sm" onClick={reset}>
            Reset preferences
          </Button>
        </div>
      </FieldGroup>
    </Panel>
  )
}

function SessionTab() {
  const { identity, authEnabled, capabilities, signOut } = useAuth()
  const version = useVersion()
  const persistence = credentials.persistence()
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel
        title="Signed-in identity"
        description={authEnabled ? "Resolved by the gateway from your API key" : "Gateway auth is disabled"}
        actions={
          authEnabled && identity ? (
            <Button variant="outline" size="sm" onClick={signOut}>
              <LogOut data-icon="inline-start" /> Sign out
            </Button>
          ) : undefined
        }
      >
        {identity ? (
          <KeyValueGrid
            items={[
              { label: "Key ID", value: identity.key_id ?? "—", mono: true },
              { label: "Key", value: maskKey(credentials.get()), mono: true },
              { label: "Org", value: identity.org_id },
              { label: "Team", value: identity.team_id ?? "—" },
              { label: "User", value: identity.user_id ?? "—" },
              { label: "Role", value: identity.role ?? "none" },
              { label: "Flags", value: [identity.ops_admin && "ops_admin", identity.cost_admin && "cost_admin"].filter(Boolean).join(", ") || "—" },
              { label: "Stored", value: persistence === "local" ? "On this device" : "This tab only" },
            ]}
          />
        ) : (
          <Alert>
            <ShieldAlert />
            <AlertTitle>Open mode</AlertTitle>
            <AlertDescription>
              Anyone who can reach this port can read operator views. Enable <code className="text-xs">auth.enabled</code> to require keys, unlock key
              management and config reload, and attribute usage.
            </AlertDescription>
          </Alert>
        )}
      </Panel>
      <Panel title="Rights" description="What this key may do in the console">
        <ul className="space-y-2.5 text-sm">
          {(
            [
              ["view_ops", "Read operator views"],
              ["reload", "Reload gateway configuration"],
              ["manage_keys", "Create and delete API keys"],
              ["read_all_cost", "Read usage and cost for every org"],
            ] as const
          ).map(([cap, label]) => (
            <li key={cap} className="flex items-center justify-between">
              <span>{label}</span>
              <StatusBadge tone={capabilities[cap] ? "good" : "neutral"} size="sm">
                {capabilities[cap] ? "Allowed" : "No"}
              </StatusBadge>
            </li>
          ))}
        </ul>
        <div className="mt-4 border-t pt-3 text-xs text-muted-foreground">
          Gateway {gatewayOrigin()} · Meridian {version.data ? `v${version.data.version} · API ${version.data.api}` : "—"}
        </div>
      </Panel>
    </div>
  )
}

function ControlTab() {
  const { settings, update } = useSettings()
  const config = useConfig()
  const qc = useQueryClient()
  const [url, setUrl] = useState(settings.controlPlaneUrl)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [testing, setTesting] = useState(false)
  const suggested = config.data?.control_plane.url

  const test = async (target: string) => {
    setTesting(true)
    setResult(null)
    try {
      const api = controlApi(target)
      await api.health()
      const ov = await api.overview()
      setResult({ ok: true, message: `Connected · incarnation ${ov.incarnation} · ${ov.nodes.total} node${ov.nodes.total === 1 ? "" : "s"}` })
      return true
    } catch (e) {
      setResult({ ok: false, message: e instanceof Error ? e.message : String(e) })
      return false
    } finally {
      setTesting(false)
    }
  }

  const save = async () => {
    const target = url.trim().replace(/\/+$/, "")
    if (target && !(await test(target))) return
    update({ controlPlaneUrl: target })
    void qc.invalidateQueries({ queryKey: ["control"] })
    toast.success(target ? "Control plane connected" : "Control plane disconnected")
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
      <Panel title="meridian-control" description="The fleet plane: GPU node enrollment, desired state, leases and the serving projection.">
        <FieldGroup className="gap-5">
          <Field data-invalid={result?.ok === false}>
            <FieldLabel htmlFor="cp-url">Base URL</FieldLabel>
            <div className="flex gap-2">
              <Input id="cp-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://control.internal:8443 or /control" className="font-mono text-sm" />
              <Button variant="outline" onClick={() => void test(url.trim())} disabled={!url.trim() || testing}>
                {testing ? <Spinner data-icon="inline-start" /> : <PlugZap data-icon="inline-start" />}
                Test
              </Button>
            </div>
            <FieldDescription>
              A full URL (CORS must allow this console's origin) or a same-origin path your reverse proxy forwards to meridian-control.
              {suggested && suggested !== url && (
                <>
                  {" "}
                  The gateway syncs from{" "}
                  <button type="button" className="font-mono underline underline-offset-2" onClick={() => setUrl(suggested)}>
                    {suggested}
                  </button>
                  .
                </>
              )}
            </FieldDescription>
          </Field>
          {result && (
            <Alert variant={result.ok ? "default" : "destructive"}>
              {result.ok ? <CircleCheck /> : <Unplug />}
              <AlertTitle>{result.ok ? "Reachable" : "Connection failed"}</AlertTitle>
              <AlertDescription>{result.message}</AlertDescription>
            </Alert>
          )}
          <div className="flex gap-2">
            <Button onClick={() => void save()} disabled={testing || url.trim() === settings.controlPlaneUrl}>
              Save
            </Button>
            {settings.controlPlaneUrl && (
              <Button
                variant="ghost"
                onClick={() => {
                  setUrl("")
                  update({ controlPlaneUrl: "" })
                  setResult(null)
                }}
              >
                Disconnect
              </Button>
            )}
          </div>
        </FieldGroup>
      </Panel>
      <Panel title="Allow the console's origin" description="Set on the meridian-control process, then restart it.">
        <CodeBlock language="shell" wrap code={`MERIDIAN_CONTROL_CORS_ORIGINS=${gatewayOrigin()}`} />
        <Alert className="mt-4">
          <ShieldAlert />
          <AlertTitle>Admin endpoints are not authenticated by meridian-control</AlertTitle>
          <AlertDescription>
            Expose /admin/* only on a private network or behind an authenticating proxy. The console never sends your Meridian API key to the control plane.
          </AlertDescription>
        </Alert>
      </Panel>
    </div>
  )
}

function GatewayTab() {
  const { can, authEnabled } = useAuth()
  const config = useConfig()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const [report, setReport] = useState<ReloadReport | null>(null)
  const reload = useMutation({
    mutationFn: gateway.reload,
    onSuccess: (r) => {
      setReport(r)
      setConfirm(false)
      toast.success("Configuration reloaded", { description: r.scope === "keys" ? "Keys re-read from the keys file." : "Config re-read and swapped atomically." })
      void qc.invalidateQueries({ queryKey: ["gateway"] })
      void qc.invalidateQueries({ queryKey: ["whoami"] })
    },
    onError: (e) => {
      setConfirm(false)
      toast.error("Reload rejected — running config kept", { description: e instanceof ApiError ? e.message : String(e) })
    },
  })
  const features = config.data?.features
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel
        title="Hot reload"
        description="Re-read the config file and swap routing, keys, budgets and policies atomically. An invalid file is rejected and the running state kept. Equivalent to SIGHUP."
      >
        <div className="space-y-4">
          {!authEnabled ? (
            <Alert>
              <ShieldAlert />
              <AlertTitle>Requires auth</AlertTitle>
              <AlertDescription>The reload endpoint is only available when auth.enabled is true. Use SIGHUP meanwhile.</AlertDescription>
            </Alert>
          ) : !can("reload") ? (
            <Alert>
              <KeyRound />
              <AlertTitle>Operator or admin key required</AlertTitle>
              <AlertDescription>Your key may view configuration but not reload it.</AlertDescription>
            </Alert>
          ) : (
            <Button onClick={() => setConfirm(true)} disabled={reload.isPending}>
              {reload.isPending ? <Spinner data-icon="inline-start" /> : <RefreshCw data-icon="inline-start" />}
              Reload configuration
            </Button>
          )}
          {config.data && !config.data.reloadable && (
            <p className="text-xs text-muted-foreground">This process was not started from a config file; reload re-reads keys only.</p>
          )}
          {report && (
            <div className="space-y-2">
              <div className="text-sm font-medium">Last reload report</div>
              <JsonView value={report} maxHeight={240} />
            </div>
          )}
        </div>
      </Panel>
      <Panel title="Enabled features" description="From the effective configuration">
        {features ? (
          <ul className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(features).map(([k, on]) => (
              <li key={k} className="flex items-center justify-between rounded-md border px-3 py-2">
                <span>{titleCase(k)}</span>
                <StatusBadge tone={on ? "good" : "neutral"} size="sm" icon={null}>
                  {on ? "On" : "Off"}
                </StatusBadge>
              </li>
            ))}
            <li className="flex items-center justify-between rounded-md border px-3 py-2">
              <span>Auth</span>
              <StatusBadge tone={config.data?.auth.enabled ? "good" : "warning"} size="sm" icon={null}>
                {config.data?.auth.enabled ? `${config.data.auth.key_count} keys` : "Off"}
              </StatusBadge>
            </li>
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">Requires an operator-view key.</p>
        )}
      </Panel>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Reload gateway configuration?"
        description={
          <>
            <p>In-flight requests finish on their already-resolved backends. New requests use the new config immediately.</p>
            <p>Changes to control_plane settings still need a restart.</p>
          </>
        }
        confirmLabel="Reload now"
        pending={reload.isPending}
        onConfirm={() => reload.mutate()}
      />
    </div>
  )
}

export default function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") ?? "general"
  return (
    <Page>
      <PageHeader title="Settings" description="Console preferences, your session, the control-plane connection and gateway operations." />
      <Tabs value={tab} onValueChange={(t) => setParams(t === "general" ? {} : { tab: t }, { replace: true })} className="gap-4">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="session">Session</TabsTrigger>
          <TabsTrigger value="control">Control plane</TabsTrigger>
          <TabsTrigger value="gateway">Gateway</TabsTrigger>
        </TabsList>
        <TabsContent value="general">
          <GeneralTab />
        </TabsContent>
        <TabsContent value="session">
          <SessionTab />
        </TabsContent>
        <TabsContent value="control">
          <ControlTab />
        </TabsContent>
        <TabsContent value="gateway">
          <GatewayTab />
        </TabsContent>
      </Tabs>
    </Page>
  )
}
