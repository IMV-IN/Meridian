import { cn } from "@/lib/utils"

/** The Meridian mark (docs/assets/meridian-mark.svg). */
export function MeridianMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label="Meridian" className={cn("size-8 shrink-0", className)}>
      <rect width="64" height="64" rx="14" fill="#10211f" />
      <path d="M16 44V20h7l9 12 9-12h7v24h-7V31l-9 12-9-12v13z" fill="#e4f6c9" />
      <circle cx="50" cy="14" r="4" fill="#f3a712" />
    </svg>
  )
}
