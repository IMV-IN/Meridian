import type { ReactNode } from "react"

import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
  children,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  eyebrow?: ReactNode
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 pb-2">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0 space-y-1">
          {eyebrow && <div className="text-sm text-muted-foreground">{eyebrow}</div>}
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{title}</h1>
          {description && <p className="max-w-3xl text-sm text-pretty text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}

export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mx-auto flex w-full max-w-[1480px] flex-col gap-6 p-4 md:p-6 lg:px-8", className)}>{children}</div>
}

/** Card with a consistent header row (title, description, actions). */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  contentClassName,
  footer,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
  footer?: ReactNode
}) {
  return (
    // min-w-0: as a grid/flex child a panel must shrink to its track; wide
    // content (tables) scrolls inside instead of pushing the page sideways.
    <Card className={cn("min-w-0 gap-3", className)}>
      {(title || actions) && (
        <CardHeader>
          {title && <CardTitle className="text-sm font-semibold">{title}</CardTitle>}
          {description && <CardDescription className="text-xs">{description}</CardDescription>}
          {actions && <CardAction className="flex items-center gap-1.5">{actions}</CardAction>}
        </CardHeader>
      )}
      <CardContent className={cn("min-w-0", contentClassName)}>{children}</CardContent>
      {footer && <div className="border-t px-4 py-2.5 text-xs text-muted-foreground">{footer}</div>}
    </Card>
  )
}

/** Definition list in a responsive grid. */
export function KeyValueGrid({
  items,
  columns = 2,
  className,
}: {
  items: { label: ReactNode; value: ReactNode; hint?: ReactNode; mono?: boolean }[]
  columns?: 1 | 2 | 3 | 4
  className?: string
}) {
  const cols = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns]
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3", cols, className)}>
      {items.map((it, i) => (
        <div key={i} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{it.label}</dt>
          <dd className={cn("mt-0.5 truncate text-sm font-medium", it.mono && "font-mono text-[13px]")}>{it.value}</dd>
          {it.hint && <dd className="mt-0.5 text-xs text-muted-foreground">{it.hint}</dd>}
        </div>
      ))}
    </dl>
  )
}

export function TagList({ tags, max = 6, className }: { tags: string[]; max?: number; className?: string }) {
  if (!tags.length) return <span className="text-muted-foreground">—</span>
  const shown = tags.slice(0, max)
  const rest = tags.length - shown.length
  return (
    <span className={cn("inline-flex flex-wrap gap-1", className)}>
      {shown.map((t) => (
        <span
          key={t}
          className="inline-flex h-5 items-center rounded border bg-muted/60 px-1.5 font-mono text-[11px] text-muted-foreground"
        >
          {t}
        </span>
      ))}
      {rest > 0 && <span className="text-[11px] text-muted-foreground">+{rest}</span>}
    </span>
  )
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-[12.5px]", className)}>{children}</span>
}
