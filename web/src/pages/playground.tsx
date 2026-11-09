import { Bot, CornerDownLeft, Dices, Eraser, Square, UserRound } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router"

import { SnippetTabs } from "@/components/code"
import { Hint } from "@/components/misc"
import { Page, PageHeader, Panel } from "@/components/page"
import { StatusBadge, httpStatusTone } from "@/components/status-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useConfig, useModels } from "@/hooks/queries"
import { ApiError } from "@/lib/api/client"
import { chatOnce, chatStream, gatewayOrigin, type ChatResult } from "@/lib/api/gateway"
import type { ChatMessage, MeridianResponseMeta } from "@/lib/api/types"
import { backendLabel, formatMs, formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useAuth } from "@/providers/auth-provider"

interface Turn {
  id: string
  role: "user" | "assistant"
  content: string
  pending?: boolean
  error?: ApiError
  result?: ChatResult
  meta?: MeridianResponseMeta
}

function snippets(model: string, stream: boolean, maxTokens: number, session: string, sessionHeader: string, authEnabled: boolean) {
  const base = `${gatewayOrigin()}/v1`
  const auth = authEnabled ? ` \\\n  -H "Authorization: Bearer $MERIDIAN_API_KEY"` : ""
  const sess = session ? ` \\\n  -H "${sessionHeader}: ${session}"` : ""
  const body = JSON.stringify({ model, stream, max_tokens: maxTokens, messages: [{ role: "user", content: "Hello!" }] })
  return [
    {
      id: "curl",
      label: "cURL",
      language: "shell",
      code: `curl -i ${base}/chat/completions${auth}${sess} \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`,
    },
    {
      id: "python",
      label: "Python",
      language: "python",
      code: `from openai import OpenAI

client = OpenAI(base_url="${base}", api_key=${authEnabled ? 'os.environ["MERIDIAN_API_KEY"]' : '"unused"'})
${session ? `extra = {"extra_headers": {"${sessionHeader}": "${session}"}}\n` : ""}resp = client.chat.completions.with_raw_response.create(
    model="${model}",
    max_tokens=${maxTokens},
    messages=[{"role": "user", "content": "Hello!"}],${session ? "\n    **extra," : ""}
)
print(resp.headers["x-meridian-backend"], resp.parse().choices[0].message.content)`,
    },
    {
      id: "node",
      label: "Node",
      language: "typescript",
      code: `import OpenAI from "openai"

const client = new OpenAI({ baseURL: "${base}", apiKey: ${authEnabled ? "process.env.MERIDIAN_API_KEY" : '"unused"'} })
const { data, response } = await client.chat.completions
  .create({ model: "${model}", max_tokens: ${maxTokens}, messages: [{ role: "user", content: "Hello!" }] }${session ? `, { headers: { "${sessionHeader}": "${session}" } }` : ""})
  .withResponse()
console.log(response.headers.get("x-meridian-backend"), data.choices[0].message.content)`,
    },
  ]
}

function MetaPanel({ turn }: { turn: Turn | undefined }) {
  const meta = turn?.result?.meta ?? turn?.meta
  const r = turn?.result
  if (!turn || (!meta && !turn.error)) {
    return <p className="text-sm text-muted-foreground">Send a message to see how Meridian routed it: backend, tier, session pin, budget headroom and timings.</p>
  }
  const rows: { label: React.ReactNode; value: React.ReactNode }[] = [
    {
      label: "Status",
      value: (
        <StatusBadge tone={httpStatusTone(turn.error?.status ?? meta?.status ?? 200)} size="sm">
          {turn.error?.status || meta?.status}
        </StatusBadge>
      ),
    },
    {
      label: "Backend",
      value: meta?.backend ? (
        <Link className="underline-offset-4 hover:underline" to={`/backends/${encodeURIComponent(meta.backend)}`}>
          {backendLabel(meta.backend).primary}
        </Link>
      ) : (
        "—"
      ),
    },
    { label: "Request ID", value: <span className="font-mono text-xs">{meta?.requestId ?? "—"}</span> },
    { label: <Hint content="Workload tier chosen from estimated prompt size and max_tokens.">Tier</Hint>, value: meta?.tier ?? "—" },
    { label: <Hint content="hit = pinned backend reused; new = first pin; remap = previous pin unhealthy.">Session route</Hint>, value: meta?.sessionRoute ?? "—" },
    { label: "Budget left · tokens", value: meta?.budgetRemainingTokens ?? "—" },
    { label: "Budget left · requests", value: meta?.budgetRemainingRequests ?? "—" },
    { label: "Time to first token", value: r?.ttftMs != null ? formatMs(r.ttftMs) : "—" },
    { label: "Total", value: r ? formatMs(r.totalMs) : "—" },
    {
      label: "Tokens",
      value: r?.usage
        ? `${formatNumber(r.usage.prompt_tokens ?? 0)} in · ${formatNumber(r.usage.completion_tokens ?? 0)} out`
        : "—",
    },
  ]
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
      {rows.map((row, i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="truncate text-right">{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export default function PlaygroundPage() {
  const [params] = useSearchParams()
  const models = useModels()
  const config = useConfig()
  const { authEnabled, identity } = useAuth()
  const modelIds = useMemo(() => (models.data?.data ?? []).map((m) => m.id), [models.data])
  const [modelChoice, setModel] = useState(params.get("model") ?? "")
  const model = modelChoice || identity?.allowed_models[0] || modelIds[0] || ""
  const [stream, setStream] = useState(true)
  const [maxTokens, setMaxTokens] = useState(256)
  const [temperature, setTemperature] = useState(0.7)
  const [system, setSystem] = useState("You are a concise, helpful assistant.")
  const [session, setSession] = useState("")
  const [input, setInput] = useState("")
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const sessionHeader = config.data?.session_affinity.header ?? "x-meridian-session"

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [turns])

  useEffect(() => () => abortRef.current?.abort(), [])

  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant")

  const send = async () => {
    const text = input.trim()
    if (!text || !model || busy) return
    const userTurn: Turn = { id: crypto.randomUUID(), role: "user", content: text }
    const asstId = crypto.randomUUID()
    const history: ChatMessage[] = [
      ...(system.trim() ? [{ role: "system" as const, content: system.trim() }] : []),
      ...turns.filter((t) => !t.error && !t.pending).map((t) => ({ role: t.role, content: t.content })),
      { role: "user", content: text },
    ]
    setTurns((ts) => [...ts, userTurn, { id: asstId, role: "assistant", content: "", pending: true }])
    setInput("")
    setBusy(true)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const patch = (p: Partial<Turn>) => setTurns((ts) => ts.map((t) => (t.id === asstId ? { ...t, ...p } : t)))
    const body = { model, messages: history, max_tokens: maxTokens, temperature }
    try {
      const result = stream
        ? await chatStream(body, { onMeta: (meta) => patch({ meta }), onDelta: (content) => patch({ content }) }, { signal: ctrl.signal, sessionId: session || undefined, sessionHeader })
        : await chatOnce(body, { signal: ctrl.signal, sessionId: session || undefined, sessionHeader })
      patch({ content: result.content, result, pending: false })
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") patch({ pending: false, content: "(stopped)" })
      else patch({ pending: false, error: e instanceof ApiError ? e : new ApiError(String(e), { status: 0 }) })
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }

  return (
    <Page className="max-w-[1480px]">
      <PageHeader
        title="Playground"
        description="Send chat completions through the gateway with your signed-in key — exactly like an application would. Conversations stay in this tab; Meridian never logs prompt content."
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Panel
          title="Conversation"
          actions={
            <Button variant="ghost" size="xs" onClick={() => setTurns([])} disabled={!turns.length || busy}>
              <Eraser data-icon="inline-start" /> Clear
            </Button>
          }
          contentClassName="flex flex-col gap-3"
        >
          <div ref={scrollRef} className="scrollbar-thin h-[52vh] min-h-80 space-y-4 overflow-y-auto rounded-lg border bg-muted/30 p-4">
            {turns.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                <Bot className="size-6" />
                <p>Ask anything. Try a long prompt to see tiering, or set a session id to watch affinity pin a backend.</p>
              </div>
            )}
            {turns.map((t) => (
              <div key={t.id} className={cn("flex gap-3", t.role === "user" && "flex-row-reverse")}>
                <span
                  className={cn(
                    "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full",
                    t.role === "user" ? "bg-primary text-primary-foreground" : "bg-card ring-1 ring-border",
                  )}
                >
                  {t.role === "user" ? <UserRound className="size-3.5" /> : <Bot className="size-3.5" />}
                </span>
                <div className={cn("max-w-[80%] space-y-1.5", t.role === "user" && "items-end text-right")}>
                  {t.error ? (
                    <Alert variant="destructive" className="text-left">
                      <AlertTitle>
                        {t.error.status ? `${t.error.status} · ` : ""}
                        {t.error.type ?? "Request failed"}
                      </AlertTitle>
                      <AlertDescription>{t.error.message}</AlertDescription>
                    </Alert>
                  ) : (
                    <div
                      className={cn(
                        "inline-block rounded-2xl px-3.5 py-2 text-left text-sm whitespace-pre-wrap",
                        t.role === "user" ? "bg-primary text-primary-foreground" : "bg-card ring-1 ring-border",
                      )}
                    >
                      {t.content || (t.pending ? <Spinner /> : <span className="text-muted-foreground">(empty)</span>)}
                      {t.pending && t.content && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground/60 align-middle" />}
                    </div>
                  )}
                  {t.role === "assistant" && (t.result || t.meta) && (
                    <div className="flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
                      {(t.result?.meta.backend ?? t.meta?.backend) && <span>via {backendLabel((t.result?.meta.backend ?? t.meta?.backend)!).primary}</span>}
                      {t.result?.ttftMs != null && <span>TTFT {formatMs(t.result.ttftMs)}</span>}
                      {t.result && <span>{formatMs(t.result.totalMs)}</span>}
                      {t.result?.usage?.completion_tokens != null && <span>{t.result.usage.completion_tokens} tokens</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void send()
            }}
            className="relative"
          >
            <Textarea
              name="message"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault()
                  void send()
                }
              }}
              placeholder={model ? `Message ${model}…` : "Pick a model first"}
              className="min-h-24 resize-none pr-28"
              aria-label="Message"
            />
            <div className="absolute right-2 bottom-2 flex items-center gap-2">
              <span className="hidden text-[11px] text-muted-foreground sm:inline">
                <Kbd>⌘</Kbd> <Kbd>↵</Kbd>
              </span>
              {busy ? (
                <Button type="button" size="sm" variant="outline" onClick={() => abortRef.current?.abort()}>
                  <Square data-icon="inline-start" /> Stop
                </Button>
              ) : (
                <Button type="submit" size="sm" disabled={!input.trim() || !model}>
                  Send <CornerDownLeft data-icon="inline-end" />
                </Button>
              )}
            </div>
          </form>
        </Panel>

        <div className="flex flex-col gap-4">
          <Panel title="Request">
            <FieldGroup className="gap-4">
              <Field>
                <FieldLabel htmlFor="pg-model">Model</FieldLabel>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger id="pg-model" className="w-full">
                    <SelectValue placeholder={models.isLoading ? "Loading models…" : "Select a model"} />
                  </SelectTrigger>
                  <SelectContent>
                    {modelIds.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {identity && identity.allowed_models.length > 0 && (
                  <FieldDescription>Your key may use: {identity.allowed_models.join(", ")}</FieldDescription>
                )}
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field>
                  <FieldLabel htmlFor="pg-max">Max tokens</FieldLabel>
                  <Input id="pg-max" name="max-tokens" type="number" inputMode="numeric" autoComplete="off" min={1} max={32768} value={maxTokens} onChange={(e) => setMaxTokens(Math.max(1, Number(e.target.value) || 1))} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="pg-temp">Temperature</FieldLabel>
                  <Input id="pg-temp" name="temperature" type="number" inputMode="decimal" autoComplete="off" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} />
                </Field>
              </div>
              <Field orientation="horizontal" className="items-center justify-between">
                <div>
                  <FieldLabel htmlFor="pg-stream">Stream (SSE)</FieldLabel>
                  <FieldDescription>Measures time to first token</FieldDescription>
                </div>
                <Switch id="pg-stream" checked={stream} onCheckedChange={setStream} />
              </Field>
              <Field>
                <FieldLabel htmlFor="pg-session">
                  <Hint content={`Sent as ${sessionHeader}. With session affinity on, the same id sticks to one backend while it stays healthy.`}>Session id</Hint>
                </FieldLabel>
                <InputGroup>
                  <InputGroupInput id="pg-session" name="session" autoComplete="off" spellCheck={false} value={session} onChange={(e) => setSession(e.target.value)} placeholder="e.g. conv-42 (optional)" className="font-mono text-xs" />
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton size="icon-xs" onClick={() => setSession(`pg-${crypto.randomUUID().slice(0, 8)}`)} aria-label="Generate session id">
                      <Dices />
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
                {config.data && !config.data.session_affinity.enabled && <FieldDescription>Session affinity is disabled on this gateway.</FieldDescription>}
              </Field>
              <Field>
                <FieldLabel htmlFor="pg-system">System prompt</FieldLabel>
                <Textarea id="pg-system" name="system-prompt" autoComplete="off" value={system} onChange={(e) => setSystem(e.target.value)} className="min-h-16 text-sm" />
              </Field>
            </FieldGroup>
          </Panel>
          <Panel title="Routing result" actions={busy ? <Spinner /> : undefined}>
            <MetaPanel turn={lastAssistant} />
          </Panel>
        </div>
      </div>
      <Panel title="Call it from code" description="Same request through any OpenAI-compatible SDK. Keys are read from the environment — never paste them into code.">
        <SnippetTabs snippets={snippets(model || "demo-model", stream, maxTokens, session, sessionHeader, authEnabled)} />
      </Panel>
    </Page>
  )
}
