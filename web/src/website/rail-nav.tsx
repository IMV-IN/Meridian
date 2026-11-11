// The product section's left rail: links to its blocks, with the one in view marked (aria-current).
import { useEffect, useState } from "react"

export function RailNav({ items }: { items: [id: string, label: string][] }) {
  const [current, setCurrent] = useState(items[0][0])
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setCurrent(e.target.id)
      },
      { rootMargin: "-40% 0px -55% 0px" },
    )
    for (const [id] of items) {
      const el = document.getElementById(id)
      if (el) io.observe(el)
    }
    return () => io.disconnect()
  }, [items])
  return (
    <nav className="rail" aria-label="Product sections">
      <ol>
        {items.map(([id, label]) => (
          <li key={id}>
            <a href={`#${id}`} aria-current={current === id ? "true" : undefined}>
              {label}
            </a>
          </li>
        ))}
      </ol>
      <p>
        Everything below is copied from the console and the <code>meridian</code> CLI.
      </p>
    </nav>
  )
}
