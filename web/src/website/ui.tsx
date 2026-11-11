// Small building blocks shared by the site's pages.
import { useRef, type ComponentProps, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { useCopy } from "./site-lib"

export function CopyButton({ text, label, className }: { text: string | (() => string); label: string; className?: string }) {
  const { copied, copy } = useCopy()
  return (
    <>
      <Button type="button" variant="outline" size="sm" className={cn("copy", className)} aria-label={label} onClick={() => copy(typeof text === "function" ? text() : text)}>
        {copied ? "Copied" : "Copy"}
      </Button>
      <span role="status" className="sr-only">
        {copied ? "Copied to the clipboard" : ""}
      </span>
    </>
  )
}

/** The install line with a copy chip, for the hero. */
export function CopyChip({ text, label }: { text: string; label: string }) {
  return (
    <span className="copy-chip">
      <span>{text}</span>
      <CopyButton text={text} label={label} />
    </span>
  )
}

// Syntax tokens for code samples, no highlighter library: write [[k:key]] for a token of kind
// c (comment), k (key), s (string), p (prompt), d (dim output) or ok (success output).
const TOKEN = /\[\[(c|k|s|p|d|ok):([\s\S]*?)\]\]/g
const KIND = { c: "tok-c", k: "tok-k", s: "tok-s", p: "tok-p", d: "tok-dim", ok: "tok-ok" } as const
export function Tokens({ text }: { text: string }) {
  const out: ReactNode[] = []
  let at = 0
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > at) out.push(text.slice(at, m.index))
    out.push(
      <span key={m.index} className={KIND[m[1] as keyof typeof KIND]}>
        {m[2]}
      </span>,
    )
    at = m.index + m[0].length
  }
  if (at < text.length) out.push(text.slice(at))
  return <>{out}</>
}

/** A code sample with a label and a copy button. The text is focusable so keyboard users can scroll it. */
export function CodeBlock({ head, code, label, className }: { head: string; code: string; label?: string; className?: string }) {
  const pre = useRef<HTMLPreElement>(null)
  return (
    <div className={cn("code", className)}>
      <div className="code-head">
        <span>{head}</span>
        <CopyButton text={() => pre.current?.textContent ?? ""} label={`Copy ${head}`} />
      </div>
      <pre ref={pre} tabIndex={0} aria-label={label ?? head}>
        <Tokens text={code} />
      </pre>
    </div>
  )
}

/** A figure with an italic serif caption: "Figure n." then the text. */
export function Figure({ n, caption, children, className }: { n?: number; caption: ReactNode; children: ReactNode; className?: string }) {
  return (
    <figure className={cn("figure", className)}>
      {children}
      <figcaption className="caption">
        {n !== undefined && <b>Figure {n}.</b>} {caption}
      </figcaption>
    </figure>
  )
}

export function Section({ id, title, lead, children, alt, className, titleId }: { id?: string; title: ReactNode; lead?: ReactNode; children?: ReactNode; alt?: boolean; className?: string; titleId: string }) {
  return (
    <section id={id} aria-labelledby={titleId} className={cn("section", className)} style={alt ? { background: "var(--bg-alt)" } : undefined}>
      <div className="wrap">
        <h2 className="h2" id={titleId}>
          {title}
        </h2>
        {lead && <p className="lead mt-4">{lead}</p>}
        {children}
      </div>
    </section>
  )
}

/** Console recreation chrome: a window that carries the console's dark tokens. */
export function ConsoleFrame({ children, label, className, ...rest }: ComponentProps<"div"> & { label?: string }) {
  return (
    <div className={cn("console dark", className)} {...(label ? { role: "img", "aria-label": label } : {})} {...rest}>
      {children}
    </div>
  )
}

export type Tone = "ok" | "run" | "wait" | "neutral" | "bad"
export function CBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge b-${tone}`}>{children}</span>
}

