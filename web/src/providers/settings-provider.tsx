import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react"

import type { TimeZoneMode } from "@/lib/format"

export interface ConsoleSettings {
  /** Poll interval for live views, ms. */
  refreshMs: number
  /** Live updates on/off (header toggle). */
  live: boolean
  /** meridian-control base URL ("" = not connected). */
  controlPlaneUrl: string
  timeZone: TimeZoneMode
  /** Toast critical activity (backend down, canary rollback). */
  alertToasts: boolean
}

const DEFAULTS: ConsoleSettings = {
  refreshMs: 2000,
  live: true,
  controlPlaneUrl: "",
  timeZone: "local",
  alertToasts: true,
}

const STORAGE_KEY = "meridian.console.settings"

function load(): ConsoleSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULTS
    const parsed = JSON.parse(raw) as Partial<ConsoleSettings>
    return { ...DEFAULTS, ...parsed }
  } catch {
    return DEFAULTS
  }
}

interface SettingsContextValue {
  settings: ConsoleSettings
  update: (patch: Partial<ConsoleSettings>) => void
  reset: () => void
  /** Effective poll interval for react-query (false = paused). */
  interval: number | false
}

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ConsoleSettings>(load)

  const persist = useCallback((next: ConsoleSettings) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* per-viewer convenience only */
    }
  }, [])

  const update = useCallback(
    (patch: Partial<ConsoleSettings>) => {
      setSettings((prev) => {
        const next = { ...prev, ...patch }
        persist(next)
        return next
      })
    },
    [persist],
  )

  const reset = useCallback(() => {
    setSettings(DEFAULTS)
    persist(DEFAULTS)
  }, [persist])

  const value = useMemo<SettingsContextValue>(
    () => ({
      settings,
      update,
      reset,
      interval: settings.live && settings.refreshMs > 0 ? settings.refreshMs : false,
    }),
    [settings, update, reset],
  )

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>
}

export function useSettings() {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error("useSettings must be used inside <SettingsProvider>")
  return ctx
}
