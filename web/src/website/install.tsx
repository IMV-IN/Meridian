import { Check, Copy, Plus, RotateCcw, X } from "lucide-react"
import { useEffect, useMemo, useState, type ComponentProps, type ReactNode } from "react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"

import { CopyCommand } from "./copy-command"
import {
  DEFAULTS,
  ENGINES,
  installCommand,
  PII_POLICIES,
  problems,
  questions,
  STRATEGIES,
  type InstallOptions,
} from "./flags"
import { SiteFooter, SiteHeader } from "./landing"

const STORE = "meridian.install-options"

function load(): InstallOptions {
  try {
    const raw = localStorage.getItem(STORE)
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<InstallOptions>) }
  } catch {
    // private mode or corrupt value: start from the defaults
  }
  return DEFAULTS
}

function Section({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="border-t border-contour pt-7 pb-10">
      <h2 className="site-display text-3xl">{title}</h2>
      {intro && <p className="mt-2 max-w-[60ch] text-sm leading-relaxed text-muted-foreground">{intro}</p>}
      <div className="mt-6 space-y-6">{children}</div>
    </section>
  )
}

function Toggle({
  id,
  label,
  hint,
  checked,
  onChange,
}: {
  id: string
  label: string
  hint: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-6">
      <div className="grid gap-1">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        <p id={`${id}-hint`} className="max-w-[56ch] text-sm leading-relaxed text-muted-foreground">
          {hint}
        </p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} aria-describedby={`${id}-hint`} className="mt-0.5" />
    </div>
  )
}

function Choice<T extends string>({
  name,
  value,
  onChange,
  options,
  columns = 1,
}: {
  name: string
  value: T
  onChange: (v: T) => void
  options: readonly { id: T; label: string; hint?: string }[]
  columns?: 1 | 2
}) {
  return (
    <RadioGroup
      aria-label={name}
      value={value}
      onValueChange={(v) => onChange(v as T)}
      className={columns === 2 ? "grid gap-2 sm:grid-cols-2" : "grid gap-2"}
    >
      {options.map((o) => (
        <Label
          key={o.id}
          htmlFor={`${name}-${o.id}`}
          className="flex cursor-pointer items-start gap-3 rounded-lg border border-input bg-card px-3.5 py-3 font-normal has-[[data-state=checked]]:border-ring has-[[data-state=checked]]:bg-accent"
        >
          <RadioGroupItem id={`${name}-${o.id}`} value={o.id} className="mt-0.5" />
          <span className="grid gap-0.5">
            <span className="text-sm font-medium">{o.label}</span>
            {o.hint && <span className="text-sm leading-relaxed text-muted-foreground">{o.hint}</span>}
          </span>
        </Label>
      ))}
    </RadioGroup>
  )
}

function TextField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  className,
  ...rest
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  error?: string
  hint?: string
  className?: string
} & Omit<ComponentProps<typeof Input>, "onChange" | "value" | "id">) {
  const described = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <div className={className}>
      <Label htmlFor={id} className="mb-1.5 text-sm font-medium">
        {label}
      </Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={!!error}
        aria-describedby={described}
        autoComplete="off"
        spellCheck={false}
        {...rest}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-sm text-destructive">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="mt-1.5 text-sm text-muted-foreground">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

export function InstallPage() {
  const [o, setO] = useState<InstallOptions>(load)
  const set = <K extends keyof InstallOptions>(key: K, value: InstallOptions[K]) => setO((prev) => ({ ...prev, [key]: value }))
  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify(o))
    } catch {
      // not persisting is fine
    }
  }, [o])

  const origin = window.location.origin
  const command = useMemo(() => installCommand(o, origin), [o, origin])
  const asks = useMemo(() => questions(o), [o])
  const errors = useMemo(() => Object.fromEntries(problems(o).map((p) => [p.field, p.message])), [o])
  const errorCount = Object.keys(errors).length

  return (
    <div className="min-h-svh pb-20 lg:pb-0">
      <SiteHeader />
      <main className="mx-auto max-w-6xl px-4 pt-8 sm:px-8 sm:pt-12">
        <h1 className="site-display text-[clamp(2.5rem,6vw,4.5rem)]">Install a gateway</h1>
        <p className="mt-4 max-w-[62ch] text-lg leading-relaxed text-deep">
          Choose how the gateway should run. The command updates as you go; paste it into a terminal on the machine that will run the
          gateway. Anything secret is asked during the install and never goes in the command.
        </p>

        <div className="mt-10 grid gap-x-12 lg:grid-cols-[minmax(0,1fr)_400px]">
          <div className="min-w-0">
            <Section title="Where it runs" intro="Linux and macOS. On Windows, run the command inside WSL.">
              <Choice
                name="service"
                value={o.service}
                onChange={(v) => set("service", v)}
                options={[
                  { id: "user", label: "In the background, starting at login", hint: "A user service (systemd or launchd). No sudo needed." },
                  { id: "system", label: "As a system service", hint: "Starts at boot, for servers. Uses sudo to install the service." },
                  { id: "none", label: "Only when I start it", hint: "Prints the command that starts it." },
                ]}
              />
              <div className="grid gap-5 sm:grid-cols-[10rem_1fr]">
                <TextField id="port" label="Port" value={o.port} onChange={(v) => set("port", v)} inputMode="numeric" error={errors.port} />
                <div>
                  <span className="mb-1.5 block text-sm font-medium">Who can reach it</span>
                  <Choice
                    name="listen"
                    value={o.listen}
                    onChange={(v) => set("listen", v)}
                    columns={2}
                    options={[
                      { id: "local", label: "This machine only" },
                      { id: "all", label: "Other machines too" },
                    ]}
                  />
                </div>
              </div>
            </Section>

            <Section
              title="Model servers"
              intro="The servers the gateway sends requests to. Anything that speaks the OpenAI API works."
            >
              <Toggle
                id="detect"
                label="Look for model servers on this machine"
                hint="Checks the usual ports for Ollama, vLLM, SGLang and LM Studio, and asks before adding each one it finds."
                checked={o.detect}
                onChange={(v) => set("detect", v)}
              />
              {o.backends.map((b, i) => (
                <fieldset key={i} className="grid gap-3 rounded-lg border border-input bg-card p-4 sm:grid-cols-[1fr_9rem]">
                  <legend className="sr-only">Model server {i + 1}</legend>
                  <TextField
                    id={`backend-url-${i}`}
                    label="URL"
                    value={b.url}
                    placeholder="http://10.0.0.12:8000"
                    inputMode="url"
                    error={errors[`backend-${i}`]}
                    onChange={(v) => set("backends", o.backends.map((x, j) => (j === i ? { ...x, url: v } : x)))}
                  />
                  <div>
                    <Label htmlFor={`backend-engine-${i}`} className="mb-1.5 text-sm font-medium">
                      Engine
                    </Label>
                    <Select
                      value={b.engine}
                      onValueChange={(v) => set("backends", o.backends.map((x, j) => (j === i ? { ...x, engine: v } : x)))}
                    >
                      <SelectTrigger id={`backend-engine-${i}`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ENGINES.map((e) => (
                          <SelectItem key={e} value={e}>
                            {e === "openai" ? "Other (OpenAI API)" : e === "trtllm" ? "TensorRT-LLM" : e === "vllm" ? "vLLM" : e === "sglang" ? "SGLang" : "Ollama"}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <TextField
                    id={`backend-model-${i}`}
                    label="Model"
                    value={b.model}
                    placeholder="Asked during install if blank"
                    error={errors[`backend-model-${i}`]}
                    onChange={(v) => set("backends", o.backends.map((x, j) => (j === i ? { ...x, model: v } : x)))}
                  />
                  <div className="flex items-end">
                    <Button
                      variant="outline"
                      className="w-full"
                      onClick={() => set("backends", o.backends.filter((_, j) => j !== i))}
                      aria-label={`Remove model server ${i + 1}`}
                    >
                      <X data-icon="inline-start" /> Remove
                    </Button>
                  </div>
                </fieldset>
              ))}
              <Button variant="outline" onClick={() => set("backends", [...o.backends, { url: "", engine: "vllm", model: "" }])}>
                <Plus data-icon="inline-start" /> Add a model server
              </Button>
            </Section>

            <Section title="Routing" intro="How the gateway picks a server for each request, and what it does when one fails.">
              <Choice name="strategy" value={o.strategy} onChange={(v) => set("strategy", v)} options={STRATEGIES} />
              <div className="grid gap-5 sm:grid-cols-2">
                <TextField
                  id="healthInterval"
                  label="Health check every (seconds)"
                  value={o.healthInterval}
                  onChange={(v) => set("healthInterval", v)}
                  inputMode="decimal"
                  error={errors.healthInterval}
                />
                <TextField
                  id="retries"
                  label="Retries on another server"
                  value={o.retries}
                  onChange={(v) => set("retries", v)}
                  inputMode="numeric"
                  error={errors.retries}
                  hint="0 sends each request once."
                />
              </div>
              <Toggle
                id="circuitBreaker"
                label="Stop sending to a server after repeated errors"
                hint="A circuit breaker: after 5 failures in a row the server is skipped for 30 seconds."
                checked={o.circuitBreaker}
                onChange={(v) => set("circuitBreaker", v)}
              />
              <Toggle
                id="sessionAffinity"
                label="Keep a conversation on one server"
                hint="Requests with the same x-meridian-session header go to the same server while it's healthy, reusing its cache."
                checked={o.sessionAffinity}
                onChange={(v) => set("sessionAffinity", v)}
              />
              <Toggle
                id="tiering"
                label="Separate pools for long prompts and long answers"
                hint="Routes by request shape to servers tagged prefill-pool, decode-pool or general. Tag your servers in the config after installing."
                checked={o.tiering}
                onChange={(v) => set("tiering", v)}
              />
            </Section>

            <Section title="Access" intro="Who can call the gateway and sign in to its console.">
              <Choice
                name="auth"
                value={o.auth}
                onChange={(v) => set("auth", v)}
                options={[
                  { id: "ask", label: "Ask me during the install" },
                  { id: "on", label: "Require API keys", hint: "Creates an admin key for the console and a key for your apps." },
                  { id: "off", label: "No keys", hint: "Anyone who can reach the port can use it. Fine on a laptop." },
                ]}
              />
              <Toggle
                id="cloud"
                label="Connect to this Meridian Cloud"
                hint={`Reports health to ${window.location.host} and lets your team sign in with their accounts. The installer asks for a gateway token.`}
                checked={o.cloud}
                onChange={(v) => set("cloud", v)}
              />
              {o.cloud && (
                <div className="grid gap-5 border-l-2 border-contour pl-5 sm:grid-cols-2">
                  <TextField
                    id="gatewayName"
                    label="Gateway name"
                    value={o.gatewayName}
                    onChange={(v) => set("gatewayName", v)}
                    placeholder="Asked during install"
                    error={errors.gatewayName}
                  />
                  <TextField
                    id="consoleUrl"
                    label="Console address"
                    value={o.consoleUrl}
                    onChange={(v) => set("consoleUrl", v)}
                    placeholder="e.g. http://gw.local:8080"
                    inputMode="url"
                    error={errors.consoleUrl}
                    hint="Where people open the gateway's console. Sign-in returns here."
                  />
                  <div className="sm:col-span-2">
                    <Toggle
                      id="keySignIn"
                      label="Also allow API-key sign-in"
                      hint="A way into the console if Meridian Cloud can't be reached."
                      checked={o.keySignIn}
                      onChange={(v) => set("keySignIn", v)}
                    />
                  </div>
                </div>
              )}
            </Section>

            <Section
              title="Limits and governance"
              intro="All optional. Budgets and cost tracking need API keys, so choosing them turns keys on."
            >
              <div className="grid gap-5 sm:grid-cols-2">
                <TextField
                  id="rateLimit"
                  label="Rate limit per org (requests per second)"
                  value={o.rateLimit}
                  onChange={(v) => set("rateLimit", v)}
                  placeholder="No limit"
                  inputMode="decimal"
                  error={errors.rateLimit}
                />
                <TextField
                  id="rateBurst"
                  label="Burst"
                  value={o.rateBurst}
                  onChange={(v) => set("rateBurst", v)}
                  placeholder="Twice the rate"
                  inputMode="decimal"
                  disabled={!o.rateLimit}
                  error={errors.rateBurst}
                />
              </div>
              <div className="space-y-3">
                <span className="block text-sm font-medium">Daily token budgets</span>
                {o.budgets.map((b, i) => (
                  <div key={i} className="grid grid-cols-[1fr_1fr_auto] items-start gap-3">
                    <TextField
                      id={`budget-org-${i}`}
                      label="Org ID"
                      value={b.org}
                      placeholder="e.g. acme"
                      error={errors[`budget-org-${i}`]}
                      onChange={(v) => set("budgets", o.budgets.map((x, j) => (j === i ? { ...x, org: v } : x)))}
                    />
                    <TextField
                      id={`budget-tokens-${i}`}
                      label="Tokens per day"
                      value={b.tokens}
                      placeholder="e.g. 2000000"
                      inputMode="numeric"
                      error={errors[`budget-tokens-${i}`]}
                      onChange={(v) => set("budgets", o.budgets.map((x, j) => (j === i ? { ...x, tokens: v } : x)))}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="mt-7"
                      aria-label={`Remove budget ${i + 1}`}
                      onClick={() => set("budgets", o.budgets.filter((_, j) => j !== i))}
                    >
                      <X />
                    </Button>
                  </div>
                ))}
                <Button variant="outline" onClick={() => set("budgets", [...o.budgets, { org: "", tokens: "" }])}>
                  <Plus data-icon="inline-start" /> Add a budget
                </Button>
              </div>
              <div>
                <Label htmlFor="pii" className="mb-1.5 text-sm font-medium">
                  Personal data in prompts
                </Label>
                <Select value={o.pii || "off"} onValueChange={(v) => set("pii", v === "off" ? "" : v)}>
                  <SelectTrigger id="pii" className="w-full sm:w-96">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PII_POLICIES.map((p) => (
                      <SelectItem key={p.id || "off"} value={p.id || "off"}>
                        {p.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Toggle
                id="cost"
                label="Track cost per org and key"
                hint="Counts tokens per org, team and key. Set prices per model in the config to see money, not just tokens."
                checked={o.cost}
                onChange={(v) => set("cost", v)}
              />
              {o.cost && (
                <TextField
                  id="currency"
                  label="Currency"
                  value={o.currency}
                  onChange={(v) => set("currency", v.toUpperCase())}
                  className="w-40"
                  maxLength={3}
                  error={errors.currency}
                />
              )}
            </Section>

            <Section title="Logging">
              <Toggle
                id="auditLog"
                label="Keep a request log on disk"
                hint="One line per request: ID, model, server, status and latency. Never the prompt or the answer."
                checked={o.auditLog}
                onChange={(v) => set("auditLog", v)}
              />
            </Section>
          </div>

          <aside id="command" aria-label="Your install command" className="scroll-mt-6 lg:pt-7">
            <div className="space-y-6 lg:sticky lg:top-6">
              <div>
                <h2 className="text-sm font-medium">Your install command</h2>
                <div className="mt-2">
                  <CopyCommand command={command} />
                </div>
                {errorCount > 0 && (
                  <p role="status" className="mt-2 text-sm text-destructive">
                    {errorCount === 1 ? "1 field needs fixing" : `${errorCount} fields need fixing`}; until then the command leaves it out.
                  </p>
                )}
              </div>
              <div>
                <h2 className="text-sm font-medium">The installer will ask for</h2>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed text-muted-foreground">
                  {asks.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                  {!asks.length && <li>Nothing: it runs straight through.</li>}
                </ul>
              </div>
              <p className="text-sm leading-relaxed text-muted-foreground">
                It installs into <code className="font-mono text-[0.85em] text-foreground">~/.meridian</code> without touching system
                packages, verifies the download, then starts the gateway and prints where to open its console.{" "}
                <a href="/install.sh" className="text-foreground underline underline-offset-4">
                  Read the script
                </a>
                .
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="ghost" size="sm" onClick={() => setO(DEFAULTS)}>
                  <RotateCcw data-icon="inline-start" /> Reset choices
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <Link to="/">About Meridian</Link>
                </Button>
              </div>
            </div>
          </aside>
        </div>
      </main>
      <SiteFooter />
      {/* Phones: the command is at the end of a long page, so keep a copy button in reach. */}
      <aside
        aria-label="Copy the install command"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/95 px-4 py-3 backdrop-blur lg:hidden"
      >
        <MobileCopyBar command={command} />
      </aside>
    </div>
  )
}

function MobileCopyBar({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(t)
  }, [copied])
  return (
    <div className="flex items-center gap-3">
      <Button
        className="flex-1"
        onClick={() =>
          navigator.clipboard.writeText(command).then(
            () => setCopied(true),
            () => document.getElementById("command")?.scrollIntoView(),
          )
        }
      >
        {copied ? <Check data-icon="inline-start" /> : <Copy data-icon="inline-start" />}
        <span aria-live="polite">{copied ? "Copied" : "Copy install command"}</span>
      </Button>
      <Button variant="outline" asChild>
        <a href="#command">Show it</a>
      </Button>
    </div>
  )
}
