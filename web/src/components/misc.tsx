import { Search, X } from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { Spinner } from "@/components/ui/spinner"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { formatRelative, formatTimestamp } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useSettings } from "@/providers/settings-provider"

/** Re-render every `ms` so relative times stay fresh. */
export function useNow(ms = 15_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return now
}

export function TimeAgo({ value, className }: { value: string | number | Date | null | undefined; className?: string }) {
  useNow()
  const { settings } = useSettings()
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <time
          dateTime={new Date(value).toISOString()}
          className={cn("cursor-default whitespace-nowrap underline-offset-4 hover:underline", className)}
        >
          {formatRelative(value)}
        </time>
      </TooltipTrigger>
      <TooltipContent>{formatTimestamp(value, settings.timeZone)}</TooltipContent>
    </Tooltip>
  )
}

export function Timestamp({
  value,
  className,
  seconds = true,
  date = true,
}: {
  value: string | number | Date | null | undefined
  className?: string
  seconds?: boolean
  date?: boolean
}) {
  const { settings } = useSettings()
  return <span className={cn("tabular whitespace-nowrap", className)}>{formatTimestamp(value, settings.timeZone, { seconds, date })}</span>
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  className,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <InputGroup className={cn("h-8 w-full sm:w-64", className)}>
      <InputGroupAddon>
        <Search />
      </InputGroupAddon>
      <InputGroupInput
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      {value && (
        <InputGroupAddon align="inline-end">
          <InputGroupButton size="icon-xs" onClick={() => onChange("")} aria-label="Clear search">
            <X />
          </InputGroupButton>
        </InputGroupAddon>
      )}
    </InputGroup>
  )
}

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description: ReactNode
  confirmLabel?: string
  destructive?: boolean
  /** Require typing this exact text to enable the confirm button. */
  typeToConfirm?: string
  pending?: boolean
  onConfirm: () => void | Promise<void>
  children?: ReactNode
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  destructive,
  typeToConfirm,
  pending,
  onConfirm,
  children,
}: ConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <ConfirmBody
        key={open ? "open" : "closed"}
        title={title}
        description={description}
        confirmLabel={confirmLabel}
        destructive={destructive}
        typeToConfirm={typeToConfirm}
        pending={pending}
        onConfirm={onConfirm}
      >
        {children}
      </ConfirmBody>
    </AlertDialog>
  )
}

function ConfirmBody({
  title,
  description,
  confirmLabel = "Confirm",
  destructive,
  typeToConfirm,
  pending,
  onConfirm,
  children,
}: Omit<ConfirmDialogProps, "open" | "onOpenChange">) {
  const [typed, setTyped] = useState("")
  const blocked = typeToConfirm !== undefined && typed.trim() !== typeToConfirm
  return (
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm text-muted-foreground">{description}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        {typeToConfirm !== undefined && (
          <div className="space-y-1.5">
            <Label htmlFor="confirm-input" className="text-xs font-normal text-muted-foreground">
              Type <span className="font-mono font-medium text-foreground">{typeToConfirm}</span> to confirm
            </Label>
            <Input
              id="confirm-input"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
            />
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={blocked || pending}
            onClick={(e) => {
              e.preventDefault()
              void onConfirm()
            }}
          >
            {pending && <Spinner data-icon="inline-start" />}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
  )
}

/** Small info hover for jargon (fencing epoch, EWMA, …). */
export function Hint({ children, content }: { children: ReactNode; content: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger className="cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-4">
        {children}
      </TooltipTrigger>
      <TooltipContent className="max-w-72 text-pretty">{content}</TooltipContent>
    </Tooltip>
  )
}
