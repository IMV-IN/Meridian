// "Run something": the console's template picker (web/src/cloud/pages/jobs.tsx), with what each template prints.
import { useState } from "react"

import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"

import { CUSTOM, limitLabel, sizeLabel, TEMPLATES } from "./templates"
import { CodeBlock } from "./ui"

/** `code` in backticks becomes <code>. */
function WithCode({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, i) =>
        i % 2 ? (
          <code key={i} style={{ fontSize: 13 }}>
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  )
}

export function TemplatePicker() {
  const [id, setId] = useState(TEMPLATES[0].id)
  const options = [...TEMPLATES, CUSTOM]
  const t = TEMPLATES.find((x) => x.id === id)
  const chosen = t ?? CUSTOM
  const facts: [string, string][] = t
    ? [
        ["Type", t.kind === "service" ? "Service" : "Job"],
        ["Size", sizeLabel(t)],
        ["Stops", limitLabel(t.timeout_seconds)],
      ]
    : [
        ["Type", "Job or service"],
        ["Size", "you choose"],
        ["Stops", "no limit"],
      ]
  return (
    <div className="picker" id="templates">
      <div className="tpl-list">
        <div id="tpl-label" className="tpl-legend">
          Start from a template
        </div>
        <RadioGroup aria-labelledby="tpl-label" value={id} onValueChange={setId} className="tpl-group">
          {options.map((o) => (
            <label key={o.id} htmlFor={`tpl-${o.id}`} className="tpl">
              <RadioGroupItem id={`tpl-${o.id}`} value={o.id} className="tpl-radio" />
              <span>
                <b>{o.title}</b>
                <span>{o.summary}</span>
              </span>
            </label>
          ))}
        </RadioGroup>
      </div>
      <div className="tpl-out" aria-live="polite">
        <dl className="facts">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <CodeBlock head="terminal" code={chosen.cmd} label={`Command for ${chosen.title}`} className="tpl-cmd" />
        <p className="doc-note">
          <WithCode text={chosen.docs} />
        </p>
      </div>
    </div>
  )
}
