import { zodResolver } from "@hookform/resolvers/zod"
import { Eye, EyeOff, KeyRound, PlugZap, RefreshCw, ShieldCheck, Waypoints, Activity } from "lucide-react"
import { useState } from "react"
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
import { gatewayOrigin } from "@/lib/api/gateway"
import { useAuth } from "@/providers/auth-provider"

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

function BrandPanel() {
  return (
    <div className="relative hidden overflow-hidden bg-brand-ink text-[#f4f8ee] lg:flex lg:flex-col lg:justify-between lg:p-12">
      <div
        aria-hidden
        className="absolute inset-0 opacity-90"
        style={{
          background:
            "radial-gradient(circle at 86% 12%, rgba(243,167,18,.9) 0 .32rem, transparent .4rem), radial-gradient(circle at 92% 19%, rgba(228,246,201,.9) 0 .2rem, transparent .26rem), linear-gradient(140deg, #10211f 0%, #1c3a35 55%, #315a51 100%)",
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
        <h2 className="text-4xl leading-[1.05] font-semibold tracking-tight text-brand-lime">
          The control room for your inference fleet.
        </h2>
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
      <p className="relative text-xs text-white/45">Prompts are never stored by the console or logged by the gateway.</p>
    </div>
  )
}

export function SignInPage() {
  const { signIn, rejectedReason } = useAuth()
  const version = useVersion()
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
    <div className="grid min-h-svh lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />
      <div className="flex flex-col items-center justify-center bg-background p-6 sm:p-10">
        <div className="w-full max-w-sm space-y-8">
          <div className="flex items-center gap-3 lg:hidden">
            <MeridianMark className="size-9" />
            <div className="text-lg font-semibold tracking-tight">Meridian</div>
          </div>
          <div className="space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">Sign in to the console</h1>
            <p className="text-sm text-muted-foreground">
              This gateway has API-key authentication enabled. Use a key with a <b>viewer</b>, <b>operator</b> or{" "}
              <b>admin</b> role for operator views.
            </p>
          </div>
          {rejectedReason && !serverError && (
            <Alert>
              <KeyRound />
              <AlertTitle>Your session ended</AlertTitle>
              <AlertDescription>{rejectedReason}. The key may have been rotated or deleted.</AlertDescription>
            </Alert>
          )}
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
                    autoFocus
                    className="font-mono text-[13px]"
                    aria-invalid={!!errors.key || !!serverError}
                    {...form.register("key")}
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton
                      size="icon-xs"
                      onClick={() => setReveal((r) => !r)}
                      aria-label={reveal ? "Hide key" : "Show key"}
                    >
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
                <Checkbox
                  id="remember"
                  checked={form.watch("remember")}
                  onCheckedChange={(v) => form.setValue("remember", v === true)}
                />
                <div className="grid gap-0.5">
                  <Label htmlFor="remember" className="text-sm font-normal">
                    Remember on this device
                  </Label>
                  <span className="text-xs text-muted-foreground">Otherwise you are signed out when this tab closes.</span>
                </div>
              </div>
              <Button type="submit" className="h-10 w-full" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting && <Spinner data-icon="inline-start" />}
                Sign in
              </Button>
            </FieldGroup>
          </form>
          <p className="text-center text-xs text-muted-foreground">
            {gatewayOrigin()}
            {version.data ? ` · Meridian v${version.data.version}` : ""}
          </p>
        </div>
      </div>
    </div>
  )
}

export function UnreachablePage({ error, onRetry }: { error: ApiError | null; onRetry: () => void }) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-6">
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
    </div>
  )
}

export function BootScreen() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-4 bg-background">
      <MeridianMark className="size-10 animate-pulse" />
      <span className="text-sm text-muted-foreground">Connecting to gateway…</span>
    </div>
  )
}
