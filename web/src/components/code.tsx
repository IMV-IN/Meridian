import { Check, Copy } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Non-secure context (plain-HTTP gateway): fall back to a hidden textarea.
    try {
      const ta = document.createElement("textarea")
      ta.value = text
      ta.setAttribute("readonly", "")
      ta.style.position = "fixed"
      ta.style.opacity = "0"
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand("copy")
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export function useCopy(timeout = 1600) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const copy = useCallback(
    async (text: string, label?: string) => {
      const ok = await writeClipboard(text)
      if (ok) {
        setCopied(true)
        window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => setCopied(false), timeout)
        if (label) toast.success(`${label} copied`)
      } else {
        toast.error("Copy failed", { description: "Select the text and copy it manually." })
      }
    },
    [timeout],
  )
  return { copied, copy }
}

export function CopyButton({
  value,
  label = "Copy",
  toastLabel,
  className,
  variant = "ghost",
  size = "icon-xs",
}: {
  value: string
  label?: string
  toastLabel?: string
  className?: string
  variant?: "ghost" | "outline" | "secondary"
  size?: "icon-xs" | "icon-sm" | "icon"
}) {
  const { copied, copy } = useCopy()
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={variant}
          size={size}
          className={cn("text-muted-foreground hover:text-foreground", className)}
          onClick={(e) => {
            e.stopPropagation()
            void copy(value, toastLabel)
          }}
          aria-label={label}
        >
          {copied ? <Check className="text-good-ink" /> : <Copy />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? "Copied" : label}</TooltipContent>
    </Tooltip>
  )
}

/** Inline monospace value with a copy affordance. */
export function CopyText({ value, display, className }: { value: string; display?: ReactNode; className?: string }) {
  return (
    <span className={cn("group/copy inline-flex max-w-full min-w-0 items-center gap-1", className)}>
      <span className="truncate font-mono text-[12.5px]">{display ?? value}</span>
      <CopyButton value={value} className="opacity-0 transition-opacity group-hover/copy:opacity-100 focus-visible:opacity-100" />
    </span>
  )
}

export function CodeBlock({
  code,
  language,
  className,
  maxHeight,
  wrap = false,
}: {
  code: string
  language?: string
  className?: string
  maxHeight?: number
  wrap?: boolean
}) {
  return (
    <div className={cn("group/code relative max-w-full min-w-0 rounded-lg border bg-muted/50", className)}>
      {language && (
        <span className="pointer-events-none absolute top-2 left-3 font-mono text-[10px] tracking-wide text-muted-foreground uppercase">
          {language}
        </span>
      )}
      <CopyButton value={code} label="Copy code" className="absolute top-1.5 right-1.5 bg-card/80 backdrop-blur" />
      <pre
        className={cn(
          "scrollbar-thin overflow-auto p-3 font-mono text-[12.5px] leading-relaxed",
          language && "pt-7",
          wrap ? "break-all whitespace-pre-wrap" : "whitespace-pre",
        )}
        style={maxHeight ? { maxHeight } : undefined}
      >
        <code>{code}</code>
      </pre>
    </div>
  )
}

export interface Snippet {
  id: string
  label: string
  language: string
  code: string
}

export function SnippetTabs({ snippets, className }: { snippets: Snippet[]; className?: string }) {
  const [active, setActive] = useState(snippets[0]?.id)
  const current = snippets.find((s) => s.id === active) ?? snippets[0]
  if (!current) return null
  return (
    <div className={cn("space-y-2", className)}>
      <Tabs value={current.id} onValueChange={setActive}>
        <TabsList className="h-8">
          {snippets.map((s) => (
            <TabsTrigger key={s.id} value={s.id} className="text-xs">
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <CodeBlock code={current.code} language={current.language} />
    </div>
  )
}

// ── JSON viewer ───────────────────────────────────────────────────────────

type Token = { text: string; cls: string }

function tokenize(json: string): Token[] {
  const tokens: Token[] = []
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],])|(\s+)/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(json))) {
    if (m.index > last) tokens.push({ text: json.slice(last, m.index), cls: "" })
    if (m[1]) {
      tokens.push({ text: m[1], cls: m[2] ? "text-[var(--series-7)]" : "text-[var(--status-good-ink)]" })
      if (m[2]) tokens.push({ text: m[2], cls: "text-muted-foreground" })
    } else if (m[3]) tokens.push({ text: m[3], cls: "text-[var(--series-2)]" })
    else if (m[4]) tokens.push({ text: m[4], cls: "text-[var(--series-1)]" })
    else if (m[5]) tokens.push({ text: m[5], cls: "text-muted-foreground" })
    else tokens.push({ text: m[0], cls: "" })
    last = re.lastIndex
  }
  if (last < json.length) tokens.push({ text: json.slice(last), cls: "" })
  return tokens
}

export function JsonView({ value, className, maxHeight = 420 }: { value: unknown; className?: string; maxHeight?: number }) {
  const text = useMemo(() => JSON.stringify(value, null, 2) ?? "null", [value])
  const tokens = useMemo(() => tokenize(text), [text])
  return (
    <div className={cn("relative max-w-full min-w-0 rounded-lg border bg-muted/40", className)}>
      <CopyButton value={text} label="Copy JSON" className="absolute top-1.5 right-1.5 z-10 bg-card/80 backdrop-blur" />
      <pre className="scrollbar-thin overflow-auto p-3 font-mono text-[12px] leading-relaxed" style={{ maxHeight }}>
        <code>
          {tokens.map((t, i) =>
            t.cls ? (
              <span key={i} className={t.cls}>
                {t.text}
              </span>
            ) : (
              t.text
            ),
          )}
        </code>
      </pre>
    </div>
  )
}
