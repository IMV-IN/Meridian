#!/usr/bin/env node
// Console UI audit: every page, in light, dark and mobile views.
//
//   - axe-core: WCAG 2.0/2.1/2.2 A + AA and best-practice rules
//   - horizontal overflow at 390px (WCAG 1.4.10 reflow)
//   - browser console errors and uncaught exceptions
//   - keyboard focus: each Tab stop must show a visible indicator
//
// Run it against the demo stack (python scripts/console_demo.py [--cloud]):
//
//   npm run audit:ui -- --mode gateway
//   npm run audit:ui -- --mode cloud --outbox /tmp/meridian-console-demo-XXXX/outbox
//
// Exits 1 when anything fails. Screenshots go to --out (default
// ./audit-ui-shots) for a visual review.
import fs from "node:fs"
import path from "node:path"
import { createRequire } from "node:module"
import { parseArgs } from "node:util"

import { chromium } from "playwright"

const require = createRequire(import.meta.url)
const AXE = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8")

const { values: args } = parseArgs({
  options: {
    mode: { type: "string", default: "gateway" },
    base: { type: "string" },
    control: { type: "string", default: "http://127.0.0.1:8443" },
    key: { type: "string", default: "mrdn_DemoAdminKey00000000000000001" },
    email: { type: "string", default: "demo@meridian.local" },
    outbox: { type: "string" },
    out: { type: "string", default: "audit-ui-shots" },
  },
})
const mode = args.mode
const base = args.base ?? (mode === "cloud" ? "http://127.0.0.1:8443" : "http://127.0.0.1:8080")
fs.mkdirSync(args.out, { recursive: true })

const VARIANTS = [
  { name: "desktop-light", viewport: { width: 1440, height: 900 }, colorScheme: "light" },
  { name: "desktop-dark", viewport: { width: 1440, height: 900 }, colorScheme: "dark" },
  { name: "mobile-light", viewport: { width: 390, height: 844 }, colorScheme: "light", isMobile: true, hasTouch: true },
]
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function axe(page) {
  await page.evaluate(AXE)
  return page.evaluate(async () => {
    const r = await window.axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] },
      resultTypes: ["violations"],
    })
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }))
  })
}

async function focusWithoutIndicator(page, steps = 25) {
  const missing = []
  await page.mouse.click(1, 1).catch(() => {})
  for (let i = 0; i < steps; i++) {
    await page.keyboard.press("Tab")
    const info = await page.evaluate(() => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const cs = getComputedStyle(el)
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none")
      return ring ? null : `${el.tagName.toLowerCase()} "${(el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40)}"`
    })
    if (info) missing.push(info)
  }
  return missing
}

// Cloud: email-code sign-in reading the dev outbox (no SMTP in the demo).
async function cloudSignIn(page, email) {
  if (!args.outbox) throw new Error("--outbox is required for --mode cloud (the demo prints its state dir; use <state>/outbox)")
  for (let attempt = 0; attempt < 3; attempt++) {
    const started = Date.now()
    await page.goto(`${base}/ui/`)
    await page.getByLabel("Work email").fill(email)
    await page.getByRole("button", { name: /Continue with email/ }).click()
    const ok = await page.getByRole("heading", { name: "Check your email" }).waitFor({ timeout: 5000 }).then(() => true, () => false)
    if (!ok) {
      await sleep(31_000) // resend spacing: one code per address every 30 s
      continue
    }
    for (let i = 0; i < 60; i++) {
      const files = fs.readdirSync(args.outbox).filter((f) => f.endsWith(`-${email.replace("@", "_at_")}.json`)).sort().reverse()
      for (const f of files) {
        const full = path.join(args.outbox, f)
        if (fs.statSync(full).mtimeMs < started - 1000) continue
        const code = JSON.parse(fs.readFileSync(full, "utf8")).subject.match(/\b(\d{6})\b/)?.[1]
        if (code) {
          await page.getByLabel("Sign-in code").fill(code)
          return
        }
      }
      await sleep(250)
    }
  }
  throw new Error(`no sign-in code for ${email}`)
}

const report = []
const browser = await chromium.launch()
try {
  // Signed-out entry page
  for (const v of VARIANTS) {
    const ctx = await browser.newContext({ ...v, bypassCSP: true })
    const page = await ctx.newPage()
    await page.goto(`${base}/ui/`)
    await page.getByRole("heading", { name: /Sign in/ }).waitFor()
    report.push({ route: "sign-in", variant: v.name, violations: await axe(page), overflow: await page.evaluate((w) => document.documentElement.scrollWidth > w, v.viewport.width) })
    await page.screenshot({ path: `${args.out}/${mode}-sign-in-${v.name}.png`, fullPage: true })
    await ctx.close()
  }

  let routes
  let storageState
  if (mode === "gateway") {
    const nodes = await (await fetch(`${args.control}/admin/nodes`)).json().catch(() => ({ nodes: [] }))
    routes = ["/", "/requests", "/metrics", "/backends", "/routing", "/playground", "/fleet", "/fleet/enrollment", "/fleet/serving", "/fleet/audit", "/keys", "/usage", "/policies", "/settings", "/settings?tab=control", "/does-not-exist"]
    if (nodes.nodes?.[0]) routes.push(`/fleet/nodes/${encodeURIComponent(nodes.nodes[0].node_id)}`)
  } else {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, bypassCSP: true })
    const page = await ctx.newPage()
    await cloudSignIn(page, args.email)
    await page.getByText("Gateways online").waitFor({ timeout: 15_000 })
    storageState = await ctx.storageState()
    const me = await page.evaluate(async () => (await fetch("/api/me")).json())
    const ws = me.workspaces[0].id
    const gws = await page.evaluate(async (w) => (await fetch(`/api/w/${w}/gateways`)).json(), ws)
    const nodes = await page.evaluate(async (w) => (await fetch(`/api/w/${w}/nodes`)).json(), ws)
    routes = ["/", "/gateways", "/gateways?connect=1", "/fleet", "/fleet/enrollment", "/fleet/serving", "/fleet/audit", "/settings", "/settings?tab=members", "/settings?tab=tokens", "/settings?tab=account", "/does-not-exist"]
    if (gws.gateways?.[0]) routes.push(`/gateways/${encodeURIComponent(gws.gateways[0].gateway_id)}`)
    if (nodes.nodes?.[0]) routes.push(`/fleet/nodes/${encodeURIComponent(nodes.nodes[0].node_id)}`)
    await ctx.close()
  }

  for (const v of VARIANTS) {
    const ctx = await browser.newContext({ ...v, bypassCSP: true, storageState })
    if (mode === "gateway") {
      await ctx.addInitScript(([control, key]) => {
        localStorage.setItem("meridian.console.settings", JSON.stringify({ controlPlaneUrl: control }))
        sessionStorage.setItem("meridian.console.apiKey", key)
      }, [args.control, args.key])
    }
    const page = await ctx.newPage()
    const errors = []
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`))
    page.on("console", (m) => {
      if (m.type() === "error" && !/status of 40[13]/.test(m.text())) errors.push(m.text().slice(0, 200))
    })
    for (const r of routes) {
      errors.length = 0
      await page.goto(`${base}/ui${r}`)
      await sleep(2000)
      const entry = { route: r, variant: v.name, violations: await axe(page), overflow: await page.evaluate((w) => document.documentElement.scrollWidth > w, v.viewport.width) }
      if (v.name === "desktop-light") entry.focus = await focusWithoutIndicator(page)
      entry.errors = [...errors]
      const slug = r.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "home"
      await page.screenshot({ path: `${args.out}/${mode}-${slug}-${v.name}.png`, fullPage: v.name !== "desktop-dark" })
      report.push(entry)
    }
    await ctx.close()
  }
} finally {
  await browser.close()
}

fs.writeFileSync(`${args.out}/${mode}-report.json`, JSON.stringify(report, null, 2))
let failures = 0
for (const p of report) {
  const where = `${p.route} [${p.variant}]`
  for (const v of p.violations) {
    failures++
    console.log(`✗ ${where}  ${v.id} (${v.impact}): ${v.help}\n    ${v.targets.join("\n    ")}`)
  }
  const extra = [
    ...(p.overflow ? ["page scrolls horizontally"] : []),
    ...(p.errors ?? []).map((e) => `console: ${e}`),
    ...(p.focus ?? []).map((f) => `no visible focus on ${f}`),
  ]
  for (const msg of extra) {
    failures++
    console.log(`✗ ${where}  ${msg}`)
  }
}
console.log(`\n${mode}: ${report.length} page views checked, ${failures ? `${failures} problem(s)` : "no problems"}. Screenshots: ${args.out}/`)
process.exit(failures ? 1 : 0)
