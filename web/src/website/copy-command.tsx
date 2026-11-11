import { Check, Copy } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** A shell command with a copy button. Long commands wrap rather than scroll. */
export function CopyCommand({ command, label = "Install command", className }: { command: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(t)
  }, [copied])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
    } catch {
      // Clipboard blocked (http or permissions): select the text instead.
      const el = document.getElementById("install-command")
      if (el) window.getSelection()?.selectAllChildren(el)
    }
  }

  return (
    <div className={cn("flex min-h-14 items-start gap-2 rounded-xl border-[1.5px] border-border bg-background py-2 pr-2 pl-4 sm:pl-5", className)}>
      <span aria-hidden className="pt-1.5 font-mono text-sm text-muted-foreground select-none">
        $
      </span>
      <code
        id="install-command"
        aria-label={label}
        translate="no"
        className="min-w-0 flex-1 pt-1.5 pb-1 font-mono text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]"
      >
        {command}
      </code>
      <Button variant="ghost" size="sm" onClick={copy} aria-label={copied ? "Copied" : "Copy command"} className="shrink-0">
        {copied ? <Check /> : <Copy />}
        <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
      </Button>
    </div>
  )
}
