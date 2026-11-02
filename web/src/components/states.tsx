import { CloudOff, Inbox, Lock, PlugZap, RefreshCw, ShieldAlert, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/lib/api/client"
import { cn } from "@/lib/utils"

import { CodeBlock } from "./code"

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  children,
  className,
  bordered = true,
}: {
  icon?: LucideIcon
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  className?: string
  bordered?: boolean
}) {
  return (
    <Empty className={cn(bordered && "border", "bg-card/40 py-10", className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {description && <EmptyDescription>{description}</EmptyDescription>}
      </EmptyHeader>
      {children && <EmptyContent>{children}</EmptyContent>}
    </Empty>
  )
}

export function ErrorState({
  error,
  onRetry,
  title,
  className,
}: {
  error: unknown
  onRetry?: () => void
  title?: string
  className?: string
}) {
  const err = error instanceof ApiError ? error : null
  let icon: LucideIcon = CloudOff
  let heading = title ?? "Something went wrong"
  let desc: ReactNode = error instanceof Error ? error.message : String(error)
  if (err?.isForbidden) {
    icon = ShieldAlert
    heading = "Insufficient permissions"
    desc = `${err.message}. Sign in with a key that has the required role.`
  } else if (err?.isNetwork) {
    icon = PlugZap
    heading = title ?? "Service unreachable"
  } else if (err?.isNotFound) {
    heading = title ?? "Not found"
  }
  return (
    <EmptyState icon={icon} title={heading} description={desc} className={className}>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw data-icon="inline-start" />
          Retry
        </Button>
      )}
    </EmptyState>
  )
}

export function PermissionState({ need, className }: { need: string; className?: string }) {
  return (
    <EmptyState
      icon={Lock}
      title="You don't have access to this view"
      description={`This page requires ${need}. Ask an administrator for a key with that right, then sign in again.`}
      className={className}
    />
  )
}

/** Feature disabled in gateway config → explain + show the config to enable it. */
export function FeatureOffState({
  icon,
  title,
  description,
  snippet,
  className,
}: {
  icon?: LucideIcon
  title: string
  description: ReactNode
  snippet?: string
  className?: string
}) {
  return (
    <EmptyState icon={icon} title={title} description={description} className={className}>
      {snippet && <CodeBlock code={snippet} language="yaml" className="w-full max-w-lg text-left" />}
    </EmptyState>
  )
}

export function LoadingRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" style={{ opacity: 1 - i * 0.1 }} />
      ))}
    </div>
  )
}

export function LoadingGrid({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-3 md:gap-4 xl:grid-cols-4", className)}>
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-28 rounded-xl" />
      ))}
    </div>
  )
}
