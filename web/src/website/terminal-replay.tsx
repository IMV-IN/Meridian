// Figure 2: a CLI session, replayed. The output format is meridian/cli/compute.py's own (`_table`, `_follow`,
// `_print_job`); the names and IDs are samples.
import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"

import { SESSION } from "./terminal-session"
import { Tokens } from "./ui"
import { useOnScreen, useReducedMotion } from "./site-lib"

export function TerminalReplay() {
  const reduced = useReducedMotion()
  const [played, setShown] = useState(0)
  const shown = reduced ? SESSION.length : played // under reduced motion the whole session is there at once
  const timer = useRef<number>(undefined)
  const started = useRef(false)

  const play = () => {
    window.clearTimeout(timer.current)
    let i = 0
    const next = () => {
      i++
      setShown(i)
      if (i < SESSION.length) timer.current = window.setTimeout(next, SESSION[i - 1][1])
    }
    next()
  }
  // Starts once, when 30% of it is on screen. Under reduced motion the whole session is there from the start.
  const ref = useOnScreen<HTMLPreElement>((visible) => {
    if (visible && !started.current && !reduced) {
      started.current = true
      play()
    }
  }, 0.3)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  return (
    <div className="term">
      <div className="term-head">
        <span>meridian compute, from a laptop on the campus network</span>
        {!reduced && (
          <Button type="button" variant="outline" size="sm" className="copy" onClick={play}>
            Replay
          </Button>
        )}
      </div>
      <pre ref={ref} tabIndex={0} aria-label="Terminal session">
        <Tokens text={SESSION.slice(0, shown).map((s) => s[0]).join("\n")} />
        {shown < SESSION.length && !reduced && (
          <>
            {"\n"}
            <span className="caret" aria-hidden="true" />
          </>
        )}
      </pre>
    </div>
  )
}
