import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { useMetrics, useStatus } from "@/hooks/queries"
import type { GatewayStatus } from "@/lib/api/types"
import {
  derivePoint,
  diffStatus,
  snapshotFromMetrics,
  type BackendPoint,
  type ConsoleEvent,
  type CounterSnapshot,
  type TrafficPoint,
} from "@/lib/telemetry"
import { useAuth } from "@/providers/auth-provider"
import { useSettings } from "@/providers/settings-provider"

const MAX_POINTS = 180
const MAX_EVENTS = 100

interface TelemetryContextValue {
  traffic: TrafficPoint[]
  backends: Record<string, BackendPoint[]>
  events: ConsoleEvent[]
  unread: number
  markRead: () => void
  clearEvents: () => void
  /** Wall-clock start of the in-browser sampling window. */
  since: number | null
}

const TelemetryContext = createContext<TelemetryContextValue | null>(null)

function push<T>(arr: T[], item: T, max = MAX_POINTS): T[] {
  const next = arr.length >= max ? arr.slice(arr.length - max + 1) : arr.slice()
  next.push(item)
  return next
}

/**
 * Samples /metrics + /meridian/status on the shared refresh cadence for as
 * long as the console is open, so live charts survive page navigation.
 * Only runs for keys that may read operator views.
 */
export function TelemetryProvider({ children }: { children: ReactNode }) {
  const { can } = useAuth()
  const { settings } = useSettings()
  const enabled = can("view_ops")
  const metrics = useMetrics(enabled)
  const status = useStatus()

  const [traffic, setTraffic] = useState<TrafficPoint[]>([])
  const [backends, setBackends] = useState<Record<string, BackendPoint[]>>({})
  const [events, setEvents] = useState<ConsoleEvent[]>([])
  const [unread, setUnread] = useState(0)
  const [since, setSince] = useState<number | null>(null)
  const prevSnap = useRef<CounterSnapshot | null>(null)
  const prevStatus = useRef<GatewayStatus | null>(null)
  const alertToasts = useRef(settings.alertToasts)
  useEffect(() => {
    alertToasts.current = settings.alertToasts
  }, [settings.alertToasts])

  useEffect(() => {
    if (!metrics.parsed || !metrics.dataUpdatedAt) return
    const snap = snapshotFromMetrics(metrics.parsed, metrics.dataUpdatedAt)
    const prev = prevSnap.current
    prevSnap.current = snap
    if (!prev) {
      setSince((s) => s ?? snap.t)
      return
    }
    const point = derivePoint(prev, snap)
    if (point) setTraffic((arr) => push(arr, point))
  }, [metrics.parsed, metrics.dataUpdatedAt])

  // Mirrors the react-query poller (an external source) into ring buffers.
  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    const cur = status.data
    if (!cur || !status.dataUpdatedAt) return
    const t = status.dataUpdatedAt
    setBackends((prev) => {
      const next: Record<string, BackendPoint[]> = {}
      for (const b of cur.backends) {
        next[b.name] = push(prev[b.name] ?? [], {
          t,
          ewma: b.ewma_latency_ms,
          inflight: b.inflight,
          queue: b.queue_depth,
        })
      }
      return next
    })
    const fresh = diffStatus(prevStatus.current, cur, t)
    prevStatus.current = cur
    if (fresh.length) {
      setEvents((arr) => [...fresh.reverse(), ...arr].slice(0, MAX_EVENTS))
      setUnread((n) => n + fresh.length)
      if (alertToasts.current) {
        for (const e of fresh) {
          if (e.severity === "critical") toast.error(e.title, { description: e.detail })
          else if (e.severity === "good") toast.success(e.title, { description: e.detail })
        }
      }
    }
  }, [status.data, status.dataUpdatedAt])
  /* oxlint-enable react/set-state-in-effect */

  const markRead = useCallback(() => setUnread(0), [])
  const clearEvents = useCallback(() => {
    setEvents([])
    setUnread(0)
  }, [])

  const value = useMemo(
    () => ({ traffic, backends, events, unread, markRead, clearEvents, since }),
    [traffic, backends, events, unread, markRead, clearEvents, since],
  )
  return <TelemetryContext.Provider value={value}>{children}</TelemetryContext.Provider>
}

export function useTelemetry() {
  const ctx = useContext(TelemetryContext)
  if (!ctx) throw new Error("useTelemetry must be used inside <TelemetryProvider>")
  return ctx
}
