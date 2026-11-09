import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import { Activity, CircleAlert, Cloud, Eye, EyeOff, KeyRound, PlugZap, RefreshCw, ShieldCheck, Waypoints } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { MeridianMark } from "@/components/layout/logo"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { useVersion } from "@/hooks/queries"
import { ApiError } from "@/lib/api/client"
import { gateway, gatewayOrigin } from "@/lib/api/gateway"
import { useAuth } from "@/providers/auth-provider"
import { autoFocusDesktop } from "@/lib/utils"

const schema = z.object({
  key: z
    .string()
    .trim()
    .min(1, "Paste your Meridian API key")
    .regex(/^mrdn_[A-Za-z0-9]{20,40}$/, "Meridian keys look like mrdn_ followed by 20–40 letters and digits"),
  remember: z.boolean(),
})
type FormValues = z.infer<typeof schema>

const HIGHLIGHTS = [
  { icon: Activity, title: "Live fleet health", body: "Backends, circuits, canaries and latency as they change." },
  { icon: Waypoints, title: "Routing you can see", body: "Tiering, affinity, isolation pools and rollout state." },
  { icon: ShieldCheck, title: "Governance built in", body: "Keys, budgets, rate limits, PII and cost attribution." },
]

export function BrandPanel() {
  return (
    <aside aria-label="About Meridian" className="relative hidden overflow-hidden bg-brand-ink text-[#f4f8ee] lg:flex lg:flex-col lg:justify-between lg:p-12">
      <div
        aria-hidden
        className="absolute inset-0 opacity-90"
        style={{
          background:
            "linear-gradient(140deg, #10211f 0%, #1c3a35 55%, #315a51 100%)",
        }}
      />
      <div aria-hidden className="absolute -right-24 -bottom-24 size-[460px] rounded-full border border-white/10" />
      <div aria-hidden className="absolute -right-8 -bottom-8 size-[300px] rounded-full border border-white/10" />
      <div className="relative flex items-center gap-3">
        <MeridianMark className="size-10 ring-1 ring-white/15 rounded-[10px]" />
        <div>
          <div className="text-lg font-semibold tracking-tight text-brand-lime">Meridian</div>
          <div className="text-xs text-white/60">Inference gateway console</div>
        </div>
      </div>
      <div className="relative max-w-md space-y-8">
        <p className="text-4xl leading-[1.05] font-semibold tracking-tight text-balance text-brand-lime">
          The control room for your inference fleet.
        </p>
        <ul className="space-y-5">
          {HIGHLIGHTS.map((h) => (
            <li key={h.title} className="flex gap-3">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/8 ring-1 ring-white/10">
                <h.icon className="size-4 text-brand-lime" />
              </span>
              <span>
                <span className="block text-sm font-medium">{h.title}</span>
                <span className="block text-sm text-white/65">{h.body}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className="relative text-xs text-white/60">Prompts are never stored by the console or logged by the gateway.</p>
    </aside>
  )
}

const CLOUD_ERRORS: Record<string, string> = {
  not_member: "Your Meridian Cloud account isn't a member of the workspace this gateway belongs to. Ask a workspace admin to invite you.",
  not_connected: "This gateway hasn't reached Meridian Cloud yet. Wait a few seconds and try again.",
  expired: "The sign-in took too long or was started in another tab. Try again.",
  unreachable: "This gateway couldn't reach Meridian Cloud to finish signing in. Check its network and try again.",
  rejected: "Meridian Cloud didn't accept this sign-in. Try again; if it keeps failing, check the gateway's token.",
  invalid: "The sign-in was cancelled or incomplete. Try again.",
  cloud_disabled: "Meridian Cloud sign-in isn't turned on for this gateway.",
}

function useCloudError(): string | null {
  // Read ?auth_error= once, then drop it from the address bar.
  const [code] = useState(() => new URLSearchParams(window.location.search).get("auth_error"))
  useEffect(() => {
    if (!code) return
    const url = new URL(window.location.href)
    url.searchParams.delete("auth_error")
    window.history.replaceState(null, "", url.pathname + url.search + url.hash)
  }, [code])
  return code ? (CLOUD_ERRORS[code] ?? CLOUD_ERRORS.invalid) : null
}

function CloudSignIn({ cloudUrl, ready }: { cloudUrl: string; ready: boolean }) {
  const host = cloudUrl.replace(/^https?:\/\//, "")
  return (
    <div className="space-y-3">
      {ready ? (
        <Button asChild className="h-10 w-full">
          <a href="/auth/cloud/start">
            <Cloud data-icon="inline-start" />
            Sign in with Meridian Cloud
          </a>
        </Button>
      ) : (
        <Button className="h-10 w-full" disabled>
          <Spinner data-icon="inline-start" />
          Connecting to Meridian Cloud…
        </Button>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {ready
          ? `Use your account at ${host} (Google or an email code). Your workspace role sets what you can do here: viewers see everything, operators can also reload, admins manage API keys.`
          : `Waiting for this gateway's first report to ${host}. This page updates by itself.`}
      </p>
    </div>
  )
}

export function SignInPage() {
  const { rejectedReason } = useAuth()
  const version = useVersion()
  const options = useQuery({
    queryKey: ["sign-in-options"],
    queryFn: ({ signal }) => gateway.signInOptions(signal),
    // Until the first report lands, poll so the button enables by itself.
    refetchInterval: (q) => (q.state.data?.cloud && !q.state.data.cloud.ready ? 3000 : false),
  })
  const cloudError = useCloudError()
  const cloud = options.data?.cloud ?? null
  const keys = options.data ? options.data.api_key : !cloud

  return (
    <div className="grid min-h-svh lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />
      <main className="flex flex-col items-center justify-center bg-background p-6 sm:p-10">
        <div className="w-full max-w-sm space-y-8">
          <div className="flex items-center gap-3 lg:hidden">
            <MeridianMark className="size-9" />
            <div className="text-lg font-semibold tracking-tight">Meridian</div>
          </div>
          <div className="space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">Sign in to this gateway</h1>
            <p className="text-sm text-muted-foreground">
              {cloud ? (
                "This gateway belongs to a Meridian Cloud workspace. Members sign in with their Meridian Cloud account."
              ) : (
                <>
                  This gateway has API-key authentication enabled. Use a key with a <b>viewer</b>, <b>operator</b> or{" "}
                  <b>admin</b> role for operator views.
                </>
              )}
            </p>
          </div>
          {cloudError ? (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Couldn't sign you in</AlertTitle>
              <AlertDescription>{cloudError}</AlertDescription>
            </Alert>
          ) : (
            rejectedReason && (
              <Alert>
                <KeyRound />
                <AlertTitle>Your session ended</AlertTitle>
                <AlertDescription>{rejectedReason}</AlertDescription>
              </Alert>
            )
          )}
          {options.isPending ? (
            <div role="status" className="flex h-10 items-center justify-center text-sm text-muted-foreground">
              <Spinner className="mr-2" /> Loading sign-in options…
            </div>
          ) : (
            <>
              {cloud && <CloudSignIn cloudUrl={cloud.cloud_url} ready={cloud.ready} />}
              {cloud && keys && (
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="h-px flex-1 bg-border" />
                  or use an API key
                  <span className="h-px flex-1 bg-border" />
                </div>
              )}
              {keys && <ApiKeyForm secondary={!!cloud} />}
            </>
          )}
          <p className="text-center text-xs text-muted-foreground">
            {gatewayOrigin()}
            {version.data ? ` · Meridian v${version.data.version}` : ""}
          </p>
        </div>
      </main>
    </div>
  )
}

function ApiKeyForm({ secondary }: { secondary: boolean }) {
  const { signIn } = useAuth()
  const [reveal, setReveal] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { key: "", remember: false } })

  const onSubmit = form.handleSubmit(async (v) => {
    setServerError(null)
    try {
      await signIn(v.key, v.remember ? "local" : "session")
    } catch (e) {
      const err = e instanceof ApiError ? e : null
      setServerError(
        err?.isUnauthorized
          ? "The gateway did not recognize this key. Check for typos or ask an admin for a new key."
          : err?.isNetwork
            ? "Cannot reach the gateway. Check that it is running and reachable from this browser."
            : (err?.message ?? "Sign-in failed."),
      )
    }
  })

  const errors = form.formState.errors
  return (
    <form onSubmit={onSubmit} noValidate>
      <FieldGroup>
        <Field data-invalid={!!errors.key || !!serverError}>
          <FieldLabel htmlFor="api-key">API key</FieldLabel>
          <InputGroup className="h-10">
            <InputGroupAddon>
              <KeyRound />
            </InputGroupAddon>
            <InputGroupInput
              id="api-key"
              type={reveal ? "text" : "password"}
              placeholder="mrdn_…"
              autoComplete="off"
              spellCheck={false}
              autoFocus={!secondary && autoFocusDesktop()}
              className="font-mono text-[13px]"
              aria-invalid={!!errors.key || !!serverError}
              {...form.register("key")}
            />
            <InputGroupAddon align="inline-end">
              <InputGroupButton size="icon-xs" onClick={() => setReveal((r) => !r)} aria-label={reveal ? "Hide key" : "Show key"}>
                {reveal ? <EyeOff /> : <Eye />}
              </InputGroupButton>
            </InputGroupAddon>
          </InputGroup>
          {errors.key ? (
            <FieldError>{errors.key.message}</FieldError>
          ) : serverError ? (
            <FieldError>{serverError}</FieldError>
          ) : (
            <FieldDescription>Sent only to this gateway as a Bearer token.</FieldDescription>
          )}
        </Field>
        <div className="flex items-start gap-2.5">
          <Checkbox id="remember" checked={form.watch("remember")} onCheckedChange={(v) => form.setValue("remember", v === true)} />
          <div className="grid gap-0.5">
            <Label htmlFor="remember" className="text-sm font-normal">
              Remember on this device
            </Label>
            <span className="text-xs text-muted-foreground">Otherwise you are signed out when this tab closes.</span>
          </div>
        </div>
        <Button
          type="submit"
          variant={secondary ? "outline" : "default"}
          className="h-10 w-full"
          disabled={form.formState.isSubmitting}
        >
          {form.formState.isSubmitting && <Spinner data-icon="inline-start" />}
          Sign in with API key
        </Button>
      </FieldGroup>
    </form>
  )
}

export function UnreachablePage({ error, onRetry }: { error: ApiError | null; onRetry: () => void }) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md">
        <CardContent className="space-y-5 pt-2">
          <div className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-lg bg-muted">
              <PlugZap className="size-5" />
            </span>
            <div>
              <h1 className="text-base font-semibold">Can't reach the gateway</h1>
              <p className="text-sm text-muted-foreground">{gatewayOrigin()}</p>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">{error?.message ?? "The gateway did not respond."}</p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            <li>Is the Meridian process running and listening on this host/port?</li>
            <li>Is a proxy or ingress in front of it stripping <code className="text-xs">/meridian/*</code> routes?</li>
          </ul>
          <Button onClick={onRetry} className="w-full">
            <RefreshCw data-icon="inline-start" />
            Try again
          </Button>
        </CardContent>
      </Card>
    </main>
  )
}

export function BootScreen() {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-4 bg-background">
      <MeridianMark className="size-10 animate-pulse" />
      <span role="status" className="text-sm text-muted-foreground">Connecting to gateway…</span>
    </main>
  )
}
