import { ArrowLeft, Mail, Terminal, TriangleAlert } from "lucide-react"
import { useEffect, useState } from "react"

import { MeridianMark } from "@/components/layout/logo"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp"
import { Spinner } from "@/components/ui/spinner"
import { ApiError } from "@/lib/api/client"
import { BrandPanel } from "@/pages/sign-in"
import { autoFocusDesktop } from "@/lib/utils"

import { cloud } from "../api"
import { useCloud } from "../session"

const AUTH_ERRORS: Record<string, string> = {
  access_denied: "Google sign-in was cancelled.",
  invalid_request: "Google sign-in didn't complete. Try again.",
  invalid_state: "That sign-in link expired. Start again.",
  signup_closed: "This Meridian is invite-only. Ask a workspace admin to invite your email address.",
  google_bad_token: "Google couldn't verify your identity. Try again.",
  google_exchange_failed: "Google sign-in failed. Try again.",
  google_no_email: "Your Google account has no verified email address.",
  account_disabled: "This account is disabled.",
  provider_disabled: "Google sign-in isn't enabled on this server.",
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" className="size-4" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 38.2 44 33 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  )
}

function message(e: unknown): string {
  if (e instanceof ApiError) return e.isNetwork ? "Can't reach Meridian Cloud. Check your connection." : e.message
  return "Something went wrong."
}

export function CloudSignIn() {
  const { info, refresh } = useCloud()
  const [step, setStep] = useState<"email" | "code">("email")
  const [email, setEmail] = useState("")
  const [code, setCode] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)
  const authError = new URLSearchParams(window.location.search).get("auth_error")

  useEffect(() => {
    if (cooldown <= 0) return
    const id = window.setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => window.clearTimeout(id)
  }, [cooldown])

  const next = window.location.pathname.startsWith("/ui") ? window.location.pathname : "/ui/"

  const sendCode = async (e?: React.FormEvent) => {
    e?.preventDefault()
    setError(null)
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("Enter a valid email address.")
      return
    }
    setBusy(true)
    try {
      await cloud.emailStart(email.trim())
      setStep("code")
      setCode("")
      setCooldown(30)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const verify = async (value: string) => {
    setError(null)
    setBusy(true)
    try {
      await cloud.emailVerify(email.trim(), value)
      window.history.replaceState(null, "", window.location.pathname)
      await refresh()
    } catch (err) {
      setError(message(err))
      setCode("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-svh lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />
      <main className="flex flex-col items-center justify-center bg-background p-6 sm:p-10">
        <div className="w-full max-w-sm space-y-7">
          <div className="flex items-center gap-3 lg:hidden">
            <MeridianMark className="size-9" />
            <div className="text-lg font-semibold tracking-tight">Meridian</div>
          </div>

          {authError && step === "email" && (
            <Alert variant="destructive">
              <TriangleAlert />
              <AlertTitle>Couldn't sign you in</AlertTitle>
              <AlertDescription>{AUTH_ERRORS[authError] ?? "Sign-in failed. Try again."}</AlertDescription>
            </Alert>
          )}

          {step === "email" ? (
            <>
              <div className="space-y-1.5">
                <h1 className="text-2xl font-semibold tracking-tight">Sign in to Meridian</h1>
                <p className="text-sm text-muted-foreground">
                  {info.signup === "invite"
                    ? "Use the email address you were invited with."
                    : "New here? Signing in creates your account."}
                </p>
              </div>
              {info.providers.google && (
                <>
                  <Button asChild variant="outline" className="h-10 w-full gap-2.5 text-sm">
                    <a href={cloud.googleStartUrl(next)}>
                      <GoogleMark /> Continue with Google
                    </a>
                  </Button>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="h-px flex-1 bg-border" />
                    or
                    <span className="h-px flex-1 bg-border" />
                  </div>
                </>
              )}
              <form onSubmit={sendCode} className="space-y-4" noValidate>
                <Field data-invalid={!!error}>
                  <FieldLabel htmlFor="email">Work email</FieldLabel>
                  <Input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    spellCheck={false}
                    inputMode="email"
                    placeholder="you@company.com"
                    className="h-10"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoFocus={!info.providers.google && autoFocusDesktop()}
                    aria-invalid={!!error}
                  />
                  {error ? <FieldError>{error}</FieldError> : <FieldDescription>We'll email you a 6-digit code.</FieldDescription>}
                </Field>
                <Button type="submit" className="h-10 w-full" disabled={busy}>
                  {busy ? <Spinner data-icon="inline-start" /> : <Mail data-icon="inline-start" />}
                  Continue with email
                </Button>
              </form>
            </>
          ) : (
            <>
              <div className="space-y-1.5">
                <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
                <p className="text-sm text-muted-foreground">
                  Enter the 6-digit code sent to <span className="font-medium text-foreground">{email.trim()}</span>. It expires in 10
                  minutes.
                </p>
              </div>
              <div className="space-y-3">
                <InputOTP
                  maxLength={6}
                  value={code}
                  onChange={(v) => {
                    setCode(v)
                    if (v.length === 6) void verify(v)
                  }}
                  disabled={busy}
                  // The person just asked for this code: focusing it is the next step.
                  autoFocus
                  name="code"
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  aria-label="Sign-in code"
                  pattern="^[0-9]*$"
                >
                  <InputOTPGroup>
                    <InputOTPSlot index={0} />
                    <InputOTPSlot index={1} />
                    <InputOTPSlot index={2} />
                  </InputOTPGroup>
                  <InputOTPSeparator />
                  <InputOTPGroup>
                    <InputOTPSlot index={3} />
                    <InputOTPSlot index={4} />
                    <InputOTPSlot index={5} />
                  </InputOTPGroup>
                </InputOTP>
                {error && <p className="text-sm text-destructive">{error}</p>}
                {busy && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Spinner /> Verifying…
                  </p>
                )}
              </div>
              <div className="flex items-center justify-between">
                <Button variant="ghost" size="sm" onClick={() => setStep("email")} disabled={busy}>
                  <ArrowLeft data-icon="inline-start" /> Different email
                </Button>
                <Button variant="link" size="sm" onClick={() => void sendCode()} disabled={busy || cooldown > 0}>
                  {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
                </Button>
              </div>
            </>
          )}

          {info.email_delivery === "console" && (
            <Alert>
              <Terminal />
              <AlertTitle>Development mode</AlertTitle>
              <AlertDescription>
                No SMTP server is configured, so sign-in codes are printed in the meridian-control logs instead of being emailed.
              </AlertDescription>
            </Alert>
          )}
          <p className="text-center text-xs text-muted-foreground">
            {window.location.host} · Meridian Cloud v{info.version}
          </p>
        </div>
      </main>
    </div>
  )
}
