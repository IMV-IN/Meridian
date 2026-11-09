import { useCallback } from "react"
import { useSearchParams } from "react-router"

/**
 * A string value kept in the URL query string, so filters and tabs survive a
 * reload and can be shared as a link. The fallback value is left out of the
 * URL to keep links short. History is replaced, not pushed: changing a filter
 * shouldn't add a Back-button step.
 */
export function useUrlState(key: string, fallback = ""): [string, (value: string) => void] {
  const [params, setParams] = useSearchParams()
  const value = params.get(key) ?? fallback
  const set = useCallback(
    (v: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (!v || v === fallback) next.delete(key)
          else next.set(key, v)
          return next
        },
        { replace: true },
      )
    },
    [key, fallback, setParams],
  )
  return [value, set]
}
