// Constants and hooks shared by the site.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"
import { useLocation } from "react-router"

export const REPO = "https://github.com/IMV-IN/Meridian"
export const DOCS = `${REPO}/tree/main/docs`
export const COMPUTE_GUIDE = `${REPO}/blob/main/docs/COMPUTE.md`
export const QUICKSTART = `${REPO}/blob/main/docs/QUICKSTART.md`

/** The installer one-liner. The control plane serves /install.sh for its own address. */
export const installCommand = (origin: string) => `curl -fsSL ${origin}/install.sh | bash`

const MOTION = "(prefers-reduced-motion: reduce)"
const subscribeMotion = (cb: () => void) => {
  const m = window.matchMedia?.(MOTION)
  m?.addEventListener?.("change", cb)
  return () => m?.removeEventListener?.("change", cb)
}
/** True when the visitor asked the OS for less motion; follows the setting live. */
export const useReducedMotion = () =>
  useSyncExternalStore(subscribeMotion, () => window.matchMedia?.(MOTION).matches ?? false, () => false)

/** Calls `cb(isVisible)` as the element enters and leaves the viewport. Does nothing where IntersectionObserver is missing. */
export function useOnScreen<T extends Element>(cb: (visible: boolean) => void, threshold = 0) {
  const ref = useRef<T>(null)
  const last = useRef(cb)
  useEffect(() => {
    last.current = cb
  })
  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === "undefined") return
    const io = new IntersectionObserver((es) => last.current(es[es.length - 1].isIntersecting), { threshold })
    io.observe(el)
    return () => io.disconnect()
  }, [threshold])
  return ref
}

/** Copies text to the clipboard and reports "copied" for 1.4 s. */
export function useCopy(ms = 1400) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const t = window.setTimeout(() => setCopied(false), ms)
    return () => window.clearTimeout(t)
  }, [copied, ms])
  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      // Clipboard blocked: the text stays selectable on the page.
    }
  }, [])
  return { copied, copy }
}

/** Set the document title and meta description for the current page, and handle #hash and scroll on navigation. */
export function usePageMeta(title: string, description: string) {
  const { pathname, hash } = useLocation()
  useEffect(() => {
    document.title = title
    document.querySelector('meta[name="description"]')?.setAttribute("content", description)
  }, [title, description])
  useEffect(() => {
    // The browser tries a #hash before React has rendered, so do it here.
    const target = hash ? document.getElementById(decodeURIComponent(hash.slice(1))) : null
    if (target) target.scrollIntoView()
    else window.scrollTo(0, 0)
  }, [pathname, hash])
}
