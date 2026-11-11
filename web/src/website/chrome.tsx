// The site's frame: skip link, sticky header with a compact menu, footer, and per-page title and meta.
import { useEffect, useRef, useState, type ReactNode } from "react"
import { Link, useLocation } from "react-router"

import { COMPUTE_GUIDE, DOCS, QUICKSTART, REPO, usePageMeta } from "./site-lib"

/** Section anchors live on the home page: on other pages they link there. */
function useAnchor() {
  const home = useLocation().pathname === "/"
  return (id: string) => (home ? `#${id}` : `/#${id}`)
}

export function Wordmark({ small }: { small?: boolean }) {
  const home = useLocation().pathname === "/"
  return (
    <a className="wordmark" href={home ? "#top" : "/"} aria-label="Meridian home" style={small ? { fontSize: 20 } : undefined}>
      {!small && (
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10.25" stroke="#F2B441" strokeWidth="1.5" />
          <path d="M12 1.75v20.5" stroke="#F2B441" strokeWidth="1.5" />
          <path d="M12 1.75c-4.2 2.8-6 6.3-6 10.25s1.8 7.45 6 10.25" stroke="#ECE6D8" strokeWidth="1.2" opacity=".55" />
        </svg>
      )}
      Meridian
    </a>
  )
}

export function SiteHeader() {
  const anchor = useAnchor()
  const [open, setOpen] = useState(false)
  const button = useRef<HTMLButtonElement>(null)
  const close = () => setOpen(false)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false)
        button.current?.focus()
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [open])

  const links = (
    <>
      <a href={anchor("product")}>Product</a>
      <Link to="/labs">Research labs</Link>
      <Link to="/teaching">Teaching</Link>
      <a href={anchor("listings")}>Marketplace</a>
      <a href={DOCS}>Docs</a>
    </>
  )
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <div className="wrap">
          <Wordmark />
          <nav className="nav" aria-label="Primary">
            {links}
          </nav>
          <div className="header-cta">
            <button ref={button} className="menu-btn" type="button" aria-expanded={open} aria-controls="mnav" onClick={() => setOpen((o) => !o)}>
              {open ? "Close" : "Menu"}
            </button>
            <a className="btn btn-ghost btn-sm" href="/ui/">
              Sign in
            </a>
            <a className="btn btn-lamp btn-sm" href={COMPUTE_GUIDE}>
              Get started
            </a>
          </div>
        </div>
        <nav className="mnav" id="mnav" aria-label="Primary, compact" hidden={!open} onClick={(e) => (e.target as HTMLElement).closest("a") && close()}>
          {links}
          <a href="/ui/">Sign in</a>
        </nav>
      </header>
    </>
  )
}

export function SiteFooter() {
  const anchor = useAnchor()
  return (
    <footer className="site-footer">
      <div className="wrap foot">
        <div>
          <Wordmark small />
          <p style={{ marginTop: 8, maxWidth: "36ch" }}>Open source. Prices and bills in rupees.</p>
        </div>
        <div>
          <h2>Product</h2>
          <ul>
            <li>
              <a href={anchor("gpu-cloud")}>Campus GPU cloud</a>
            </li>
            <li>
              <a href={anchor("llm")}>LLM endpoint</a>
            </li>
            <li>
              <a href={anchor("listings")}>Marketplace</a>
            </li>
            <li>
              <Link to="/install">Install a gateway</Link>
            </li>
          </ul>
        </div>
        <div>
          <h2>For</h2>
          <ul>
            <li>
              <Link to="/labs">Research labs</Link>
            </li>
            <li>
              <Link to="/teaching">Teaching</Link>
            </li>
          </ul>
        </div>
        <div>
          <h2>Docs</h2>
          <ul>
            <li>
              <a href={QUICKSTART}>Quickstart</a>
            </li>
            <li>
              <a href={COMPUTE_GUIDE}>Compute guide</a>
            </li>
            <li>
              <a href={REPO}>Source on GitHub</a>
            </li>
            <li>
              <a href="/ui/">Console sign-in</a>
            </li>
          </ul>
        </div>
      </div>
    </footer>
  )
}

/** A full page: header, main, footer. */
export function SitePage({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  usePageMeta(title, description)
  return (
    <>
      <SiteHeader />
      <main id="main">{children}</main>
      <SiteFooter />
    </>
  )
}
