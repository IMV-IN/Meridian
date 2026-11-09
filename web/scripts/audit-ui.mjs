#!/usr/bin/env node
// Console UI audit: every page, in light, dark and mobile views.
//
//   - axe-core: WCAG 2.0/2.1/2.2 A + AA and best-practice rules
//   - horizontal overflow at 390px (WCAG 1.4.10 reflow)
//   - browser console errors and uncaught exceptions
//   - keyboard focus: each Tab stop must show a visible indicator
//   - containment: no element spills out of its card, dialog, menu or
//     control, and no text is cut off without an ellipsis
//   - overlays: every dialog, select and menu a page can open is opened and
//     put through the same checks (axe, containment, reflow)
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
    only: { type: "string" }, // regex: audit just the matching routes
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

async function axe(page, selector) {
  // Contrast is measured on what is painted: let fades and slides finish so a
  // toast caught mid-fade-in isn't reported (infinite spinners are ignored).
  await page
    .waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getTiming().iterations === Infinity), null, { timeout: 3000 })
    .catch(() => {})
  await page.evaluate(AXE)
  return page.evaluate(async (sel) => {
    // With a modal open the rest of the page is aria-hidden on purpose, so
    // check just the overlay then.
    const ctx = sel ? { include: [...document.querySelectorAll(sel)].map((el) => [el]) } : document
    const r = await window.axe.run(ctx, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] },
      resultTypes: ["violations"],
    })
    return r.violations
      .filter((v) => !sel || !["region", "landmark-one-main", "page-has-heading-one", "aria-hidden-focus"].includes(v.id))
      .map((v) => ({ id: v.id, impact: v.impact, help: v.help, targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }))
  }, selector)
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

// Elements that stick out of the box they are drawn in. Page-level overflow
// misses these when the page itself is wide enough (a select wider than its
// dialog, a badge past a card's edge).
async function containment(page) {
  return page.evaluate(() => {
    const BOXES = '[data-slot="dialog-content"],[data-slot="alert-dialog-content"],[data-slot="sheet-content"],[data-slot="card"],[data-slot="popover-content"],[data-slot="select-content"],[data-slot="dropdown-menu-content"],[data-slot="select-trigger"],[data-slot="button"],[data-slot="input"],[data-slot="badge"],[data-slot="tabs-list"],[role="dialog"]'
    const out = []
    const label = (el) => {
      const slot = el.getAttribute("data-slot")
      const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 50)
      return `${el.tagName.toLowerCase()}${slot ? `[${slot}]` : ""} "${text}"`
    }
    const visible = (el) => {
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity) > 0
    }
    for (const box of document.querySelectorAll(BOXES)) {
      if (!visible(box)) continue
      const b = box.getBoundingClientRect()
      const bcs = getComputedStyle(box)
      // A box that scrolls its own content is fine; it is the content that must fit.
      const scrollsX = /(auto|scroll)/.test(bcs.overflowX)
      if (!scrollsX && box.scrollWidth > box.clientWidth + 1 && bcs.overflowX !== "hidden" && bcs.overflowX !== "clip") {
        out.push(`${label(box)} content is ${box.scrollWidth - box.clientWidth}px wider than the box`)
        continue
      }
      for (const el of box.querySelectorAll("*")) {
        if (!visible(el)) continue
        // Stop at nested scrollers and fixed/absolute decorations (close buttons, indicators).
        const cs = getComputedStyle(el)
        if (cs.position === "fixed") continue
        const r = el.getBoundingClientRect()
        let clipped = false
        for (let a = el.parentElement; a && a !== box; a = a.parentElement) {
          const acs = getComputedStyle(a)
          if (/(auto|scroll|hidden|clip)/.test(acs.overflowX)) { clipped = true; break }
        }
        if (clipped || scrollsX) continue
        if (r.right > b.right + 1 || r.left < b.left - 1) {
          out.push(`${label(el)} sticks out of ${label(box)} by ${Math.round(Math.max(r.right - b.right, b.left - r.left))}px`)
          break
        }
      }
    }
    // Text cut off with no ellipsis (overflow hidden, single line).
    for (const el of document.querySelectorAll("body *")) {
      if (!visible(el) || el.children.length) continue
      const cs = getComputedStyle(el)
      if (parseFloat(cs.width) <= 1 || (cs.clipPath && cs.clipPath !== "none") || (cs.clip && cs.clip !== "auto")) continue // screen-reader-only text
      if (cs.overflowX === "hidden" && cs.textOverflow !== "ellipsis" && cs.whiteSpace === "nowrap" && el.scrollWidth > el.clientWidth + 1)
        out.push(`${label(el)} text is cut off without an ellipsis`)
    }
    // Placeholders wider than their input are clipped mid-word.
    const ctx = document.createElement("canvas").getContext("2d")
    for (const el of document.querySelectorAll("input[placeholder], textarea[placeholder]")) {
      if (!visible(el) || el.value) continue
      const cs = getComputedStyle(el)
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
      const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
      if (el.tagName === "INPUT" && ctx.measureText(el.placeholder).width > inner + 1) out.push(`${label(el)} placeholder "${el.placeholder}" is cut off`)
    }
    // Anything (in an overlay) past the viewport edge.
    for (const el of document.querySelectorAll('[role="dialog"], [role="listbox"], [role="menu"]')) {
      const r = el.getBoundingClientRect()
      if (visible(el) && (r.right > innerWidth + 1 || r.left < -1)) out.push(`${label(el)} goes past the edge of the screen`)
    }
    return [...new Set(out)]
  })
}

// Open everything a page can open: dialogs (by trigger or by action buttons
// that open a form or a confirmation), selects and menus, and check each one.
// Runs against the disposable demo stack; confirmations are never accepted.
const OPENERS = /^(invite|connect|create|new|mint|add|edit|rotate|revoke|remove|delete|disable|enable|drain|cordon|change|rename|issue|reset|transfer|leave|authorize|publish|record)\b/i
async function overlays(page, viewportWidth, shot) {
  const found = []
  const dialogOpen = () => page.locator('[role="dialog"]:visible, [role="alertdialog"]:visible').count()
  const checkOpen = async (what) => {
    await sleep(350)
    const problems = [...(await containment(page)), ...((await page.evaluate((w) => document.documentElement.scrollWidth > w, viewportWidth)) ? ["page scrolls horizontally"] : [])]
    const violations = await axe(page, '[role="dialog"]:not([hidden]), [role="alertdialog"], [role="listbox"], [role="menu"]')
    await page.screenshot({ path: `${shot}-${what.replace(/[^a-z0-9]+/gi, "_").slice(0, 60)}.png` }).catch(() => {})
    if (problems.length || violations.length) found.push({ what, problems, violations })
  }
  const openSelects = async (scope, where) => {
    const combos = scope.locator('[role="combobox"]:visible')
    const n = await combos.count()
    for (let i = 0; i < n; i++) {
      const c = combos.nth(i)
      const name = (await c.getAttribute("aria-label")) || (await c.textContent())?.trim() || `select ${i + 1}`
      if (!(await c.isEnabled())) continue
      await c.click().catch(() => {})
      if (!(await page.locator('[role="listbox"]:visible').count())) {
        await page.keyboard.press("Escape")
        continue
      }
      await checkOpen(`${where} → select "${name}" open`)
      // A layout can hold for the default value and break for another one
      // (a longer label), so choose each option and check again.
      const options = (await page.locator('[role="listbox"]:visible [role="option"]').allTextContents()).slice(0, 10)
      await page.keyboard.press("Escape")
      await sleep(150)
      for (let k = 0; k < options.length; k++) {
        await c.click().catch(() => {})
        const opt = page.locator('[role="listbox"]:visible [role="option"]').nth(k)
        if (!(await opt.count())) break
        await opt.click().catch(() => {})
        await sleep(200)
        const problems = await containment(page)
        if (where !== "page") await page.screenshot({ path: `${shot}-${`${where}-${name}=${options[k]}`.replace(/[^a-z0-9]+/gi, "_").slice(0, 80)}.png` }).catch(() => {})
        if (problems.length) found.push({ what: `${where} → select "${name}" = "${options[k].trim().slice(0, 40)}"`, problems, violations: [] })
      }
      await c.click().catch(() => {}) // put the first value back
      await page.locator('[role="listbox"]:visible [role="option"]').first().click().catch(() => page.keyboard.press("Escape"))
      await sleep(150)
    }
  }
  await openSelects(page, "page")
  const menus = page.locator('[aria-haspopup="menu"]:visible')
  const seenMenus = new Set()
  for (let i = 0; i < (await menus.count()); i++) {
    const m = menus.nth(i)
    const name = (await m.getAttribute("aria-label")) || (await m.textContent())?.trim() || `menu ${i + 1}`
    // Row menus ("Actions for x") repeat per row: open the first one fully.
    const kind = name.replace(/ for .*/, " for …")
    const full = !seenMenus.has(kind)
    seenMenus.add(kind)
    await m.click().catch(() => {})
    if (!(await page.locator('[role="menu"]:visible').count())) continue
    await checkOpen(`menu "${name}" open`)
    const items = full ? await page.locator('[role="menu"]:visible [role="menuitem"]').allTextContents() : []
    await page.keyboard.press("Escape")
    await sleep(150)
    for (const text of items.map((t) => t.trim()).filter((t) => OPENERS.test(t))) {
      await m.click().catch(() => {})
      await page.locator('[role="menu"]:visible [role="menuitem"]').filter({ hasText: text }).first().click().catch(() => {})
      await sleep(300)
      if (await dialogOpen()) {
        await checkOpen(`menu "${name}" → "${text}" dialog`)
        await openSelects(page.locator('[role="dialog"]:visible, [role="alertdialog"]:visible').first(), `"${text}" dialog`)
      }
      await page.keyboard.press("Escape")
      await sleep(250)
      if (await dialogOpen()) await page.keyboard.press("Escape")
    }
  }
  // The command palette (Ctrl/⌘+K) is part of every page.
  await page.keyboard.press("ControlOrMeta+k")
  await sleep(300)
  if (await dialogOpen()) {
    await checkOpen("command palette")
    await page.keyboard.press("Escape")
    await sleep(250)
  }
  const buttons = page.locator('main button:visible, main [aria-haspopup="dialog"]:visible, header [aria-haspopup="dialog"]:visible')
  const labels = []
  for (let i = 0; i < (await buttons.count()); i++) {
    const t = ((await buttons.nth(i).getAttribute("aria-label")) || (await buttons.nth(i).textContent()) || "").trim()
    const popup = await buttons.nth(i).getAttribute("aria-haspopup")
    if ((popup === "dialog" || OPENERS.test(t)) && !labels.some((l) => l.text === t)) labels.push({ i, text: t })
  }
  for (const { i, text } of labels.slice(0, 12)) {
    const b = buttons.nth(i)
    if (!(await b.isVisible().catch(() => false)) || !(await b.isEnabled().catch(() => false))) continue
    await b.click().catch(() => {})
    await sleep(300)
    if (await dialogOpen()) {
      await checkOpen(`dialog from "${text}"`)
      await openSelects(page.locator('[role="dialog"]:visible, [role="alertdialog"]:visible').first(), `dialog from "${text}"`)
      await page.keyboard.press("Escape")
      await sleep(250)
      if (await dialogOpen()) await page.keyboard.press("Escape")
    }
  }
  return found
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

  if (args.only) routes = routes.filter((r) => new RegExp(args.only).test(r))
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
      const slug = r.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "home"
      entry.containment = await containment(page)
      if (v.name === "desktop-light") entry.focus = await focusWithoutIndicator(page)
      await page.goto(`${base}/ui${r}`)
      await sleep(1500)
      entry.overlays = await overlays(page, v.viewport.width, `${args.out}/${mode}-${slug}-${v.name}`)
      entry.errors = [...errors]
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
    ...(p.containment ?? []),
    ...(p.overlays ?? []).flatMap((o) => [...o.problems.map((x) => `${o.what}: ${x}`), ...o.violations.map((v) => `${o.what}: ${v.id} (${v.impact}): ${v.help} — ${v.targets.join(", ")}`)]),
  ]
  for (const msg of extra) {
    failures++
    console.log(`✗ ${where}  ${msg}`)
  }
}
console.log(`\n${mode}: ${report.length} page views checked, ${failures ? `${failures} problem(s)` : "no problems"}. Screenshots: ${args.out}/`)
process.exit(failures ? 1 : 0)
