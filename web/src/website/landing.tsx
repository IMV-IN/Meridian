import type { ReactNode } from "react"
import { Link } from "react-router"

import { MeridianMark } from "@/components/layout/logo"
import { Button } from "@/components/ui/button"
import { formatINR } from "@/lib/format"
import { cn } from "@/lib/utils"

import { CopyCommand } from "./copy-command"

const REPO = "https://github.com/IMV-IN/Meridian"
const DOCS = `${REPO}/tree/main/docs`

const WRAP = "mx-auto w-full max-w-[1264px] px-5 sm:px-8"
const H2 = "site-display text-[34px] md:text-[52px]"

// Buttons: the shared Button, sized and shaped to the site design.
const BTN = "h-[52px] rounded-xl px-[26px] text-base font-semibold"
const BTN_SM = "h-10 rounded-[10px] px-[18px] text-[15px] font-semibold"
const GHOST =
  "border-[1.5px] border-border bg-card text-foreground hover:bg-mist hover:text-foreground dark:border-border dark:bg-card dark:hover:bg-mist"
// On the cobalt band: a white button and an outlined one, both with a white focus ring.
const ON_COBALT_PRIMARY =
  "bg-white text-night hover:bg-white/90 focus-visible:border-white focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-cobalt"
const ON_COBALT_GHOST =
  "border-[#6b83f8] bg-transparent text-white hover:bg-white/10 hover:text-white focus-visible:border-white focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-cobalt dark:border-[#6b83f8] dark:bg-transparent dark:hover:bg-white/10"

const LISTINGS = [
  { gpus: "8 × H100", memory: "80 GB each", location: "Mumbai", price: "₹185.00", available: true },
  { gpus: "4 × A100", memory: "40 GB each", location: "Hyderabad", price: "₹92.00", available: true },
  { gpus: "2 × L40S", memory: "48 GB each", location: "Pune", price: "₹64.00", available: false },
  { gpus: "16 × H100", memory: "80 GB each", location: "Chennai", price: "₹179.50", available: true },
  { gpus: "4 × RTX 4090", memory: "24 GB each", location: "Bengaluru", price: "₹38.00", available: true },
]

const WAYS = [
  {
    title: "Rent GPUs",
    lead: true,
    body: "Pick a listing and its GPUs are yours alone until you release them. Rent for one job and the rental ends when the job does.",
    points: ["Billed per second, in rupees", "One hour of credit up front, no minimum term", "The provider cannot see what you run"],
    cta: "Browse GPUs",
    href: "/ui/marketplace",
  },
  {
    title: "List your GPUs",
    body: "Enroll a server, set a price per GPU-hour and earn from capacity you are not using.",
    points: ["You keep the full price you set", "Pause or end a rental at any time"],
    cta: "List a node",
    href: "/ui/marketplace?tab=listings",
  },
  {
    title: "Run your own fleet",
    body: "Manage the servers you own from one console and run jobs and models on them at no charge.",
    points: ["An agent on each GPU host", "Jobs across several nodes"],
    cta: "Enroll a node",
    href: "/ui/fleet/enrollment",
  },
]

const FACTS = [
  { title: "Per second", body: "From the moment you rent until you release." },
  { title: "Prepaid credit", body: "A rental stops by itself when credit runs out." },
  { title: "Price locked", body: "A rental keeps the price it started at." },
  { title: "Own hardware is free", body: "Jobs on your own nodes cost nothing." },
]

const JOB_POINTS = [
  { title: "Training across several nodes", body: "Every replica starts together and is told its rank and the address of each peer." },
  { title: "Never runs twice", body: "A job is launched at most once, even if a node restarts halfway." },
  { title: "Models as services", body: "Keep a model server running on a port, with its own access token." },
]

const CAPABILITIES = [
  { title: "Picks a server for every request", body: "By load, recent latency or the size of the request." },
  { title: "Keeps answering when a server fails", body: "Health checks and retries move traffic elsewhere." },
  { title: "Shows what every request did", body: "Live traffic, latency and a request log. Never the prompt." },
  { title: "Controls who uses what", body: "API keys, rate limits, budgets and PII redaction." },
]

const PRIVACY = [
  {
    title: "Prompts never leave your network",
    body: "Gateways serve traffic where your servers are. Meridian Cloud receives health and counts, not content.",
  },
  {
    title: "A provider cannot see your work",
    body: "The host sees that a GPU is taken. It does not see your image, arguments, environment or logs.",
  },
  {
    title: "Open source",
    body: "The gateway, the control plane and the node agent are public. Read the code that runs on your hardware.",
  },
]

const FAQ = [
  {
    q: "How do I add credit?",
    a: "By bank transfer or payment link through your account manager. Credit appears against your payment reference. Card and UPI checkout are not available yet.",
  },
  {
    q: "What does a job need?",
    a: "A container image pinned by digest and a command. A node must have jobs turned on by its owner before it accepts them.",
  },
  { q: "What if a node goes offline while I am renting it?", a: "The rental ends and you are billed only up to the node's last contact." },
  { q: "What does the gateway cost?", a: "Nothing. It is open source and runs on your own machines." },
]

/** The example rental on the pricing receipt. All amounts are integer paise. */
const EXAMPLE = { gpus: 2, ratePaisePerGpuHour: 12_000, seconds: 30 * 60, feePercent: 2 }

function receipt({ gpus, ratePaisePerGpuHour, seconds, feePercent }: typeof EXAMPLE) {
  const subtotal = Math.round((gpus * ratePaisePerGpuHour * seconds) / 3600)
  const fee = Math.round((subtotal * feePercent) / 100)
  return { subtotal, fee, total: subtotal + fee, providerReceives: subtotal }
}

export function SiteHeader({ anchors = "/#" }: { anchors?: string }) {
  return (
    <header className={cn(WRAP, "flex h-[68px] items-center justify-between gap-4 md:h-[84px]")}>
      <Link to="/" className="flex items-center gap-3 font-display text-[22px] font-bold tracking-[-0.02em]">
        <MeridianMark className="size-[34px]" />
        Meridian
      </Link>
      <nav aria-label="Site" className="hidden items-center gap-[34px] text-[15.5px] font-medium text-soft lg:flex">
        {[
          ["Rent GPUs", "rent"],
          ["Pricing", "pricing"],
          ["Jobs", "jobs"],
          ["Gateway", "gateway"],
        ].map(([label, id]) => (
          <a key={id} href={`${anchors}${id}`} className="hover:text-foreground">
            {label}
          </a>
        ))}
        <a href={DOCS} className="hover:text-foreground">
          Docs
        </a>
      </nav>
      <div className="flex items-center gap-3.5">
        <a href="/ui/" className="hidden text-[15.5px] font-semibold sm:inline">
          Sign in
        </a>
        <Button asChild className={BTN_SM}>
          <a href="/ui/marketplace">Browse GPUs</a>
        </Button>
      </div>
    </header>
  )
}

export function SiteFooter() {
  return (
    <footer className="bg-band py-11 text-[15px] text-night-soft [--ring:#8da0ff] [--mark-bg:#ffffff] [--mark-glyph:#0c1230]">
      <div className={cn(WRAP, "flex flex-col items-start justify-between gap-7 md:flex-row md:items-center")}>
        <Link to="/" className="flex items-center gap-3 font-display text-[22px] font-bold tracking-[-0.02em] text-white">
          <MeridianMark className="size-[34px]" />
          Meridian
        </Link>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-4 md:gap-x-[30px]">
          <a href={DOCS} className="hover:text-white">
            Documentation
          </a>
          <a href={REPO} className="hover:text-white">
            Source on GitHub
          </a>
          <Link to="/install" className="hover:text-white">
            Install
          </Link>
          <a href="/ui/" className="hover:text-white">
            Sign in
          </a>
        </nav>
        <span>Built in India. Open source.</span>
      </div>
    </footer>
  )
}

function Pill({ available }: { available: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-[7px] rounded-full px-2.5 text-[13px] font-semibold whitespace-nowrap sm:px-[11px] sm:text-[13.5px]",
        available ? "bg-ok-bg text-ok-fg" : "bg-mist text-soft",
      )}
    >
      <i aria-hidden className={cn("size-[7px] rounded-full", available ? "bg-ok-dot" : "bg-[#9aa3c4]")} />
      {available ? "Available" : "Rented"}
    </span>
  )
}

/** Sample data only. The amber rule is the left edge of the price column. */
function ListingBoard() {
  const edge = "px-3.5 sm:px-[26px]"
  return (
    <div className="rounded-[18px] border-[1.5px] border-border bg-card shadow-[0_30px_60px_-30px_rgba(12,18,48,0.28)] sm:rounded-[22px]">
      <div className="flex flex-col gap-0.5 px-[18px] pt-[18px] pb-3 sm:flex-row sm:items-baseline sm:justify-between sm:px-[26px] sm:pt-[22px] sm:pb-4">
        <p id="listings-title" className="font-display text-xl font-semibold tracking-[-0.01em]">
          GPU listings
        </p>
        <p id="listings-note" className="text-[13.5px] text-soft">
          Sample listings. Live prices are in the console.
        </p>
      </div>
      <table aria-labelledby="listings-title" aria-describedby="listings-note" className="w-full border-collapse text-left">
        <thead className="bg-sunken text-[13px] text-soft">
          <tr className="h-[34px]">
            <th scope="col" className={cn("border-y-[1.5px] border-border font-normal sm:pr-0", edge)}>
              GPUs
            </th>
            <th scope="col" className="hidden border-y-[1.5px] border-border font-normal sm:table-cell">
              Location
            </th>
            <th scope="col" className="border-y-[1.5px] border-l-2 border-border border-l-saffron pl-4 font-normal">
              Per GPU-hour
            </th>
            <th scope="col" className={cn("border-y-[1.5px] border-border font-normal", edge)}>
              Status
            </th>
          </tr>
        </thead>
        <tbody>
          {LISTINGS.map((l) => (
            <tr key={l.gpus} className="h-[68px] border-b border-mist text-base last:border-b-0">
              <th scope="row" className={cn("text-left font-normal sm:pr-0", edge)}>
                <span className="block text-[15px] font-semibold whitespace-nowrap sm:text-base">{l.gpus}</span>
                <span className="text-[13px] text-soft whitespace-nowrap sm:text-[13.5px]">{l.memory}</span>
              </th>
              <td className="hidden pr-4 sm:table-cell">{l.location}</td>
              <td className="border-l-2 border-l-saffron pl-4 font-display text-lg font-semibold tracking-[-0.01em] tabular-nums sm:text-[22px]">
                {l.price}
              </td>
              <td className={edge}>
                <Pill available={l.available} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="rounded-b-[16px] border-t-[1.5px] border-border bg-sunken px-[18px] py-3 text-[13.5px] text-soft sm:rounded-b-[20px] sm:px-[26px] sm:py-3.5">
        The amber line is 82.5° E, the meridian India keeps time by. Renters add a 2% fee.
      </p>
    </div>
  )
}

function Node({ title, sub, core }: { title: string; sub: string; core?: boolean }) {
  return (
    <div
      className={cn(
        "rounded-[14px] border-[1.5px] p-[16px_18px] text-[15px]",
        core ? "border-night bg-night text-white dark:border-night-line" : "border-border bg-background",
      )}
    >
      <b className="block text-[16.5px] font-semibold">{title}</b>
      <span className={cn("text-[13.5px]", core ? "text-night-soft" : "text-soft")}>{sub}</span>
    </div>
  )
}

function Wire() {
  return (
    <div aria-hidden className="relative ml-6 h-7 w-0.5 bg-border lg:mx-0 lg:h-0.5 lg:w-auto">
      <i className="absolute top-[40%] -left-1 size-2.5 rounded-full bg-saffron lg:-top-1 lg:left-[42%]" />
    </div>
  )
}

function Section({ id, label, className, children }: { id?: string; label: string; className?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${label}-h`} className={className}>
      {children}
    </section>
  )
}

export function LandingPage() {
  const command = `curl -fsSL ${window.location.origin}/install.sh | bash`
  const r = receipt(EXAMPLE)
  const hours = EXAMPLE.seconds / 3600

  return (
    <div className="min-h-svh">
      <SiteHeader anchors="#" />
      <main>
        <div className={WRAP}>
          <section aria-labelledby="hero-h" className="grid items-start gap-9 pt-7 pb-16 md:gap-12 md:pt-16 md:pb-24 xl:grid-cols-[minmax(0,560fr)_minmax(0,592fr)]">
            <div>
              <h1 id="hero-h" className="site-display max-w-[14ch] text-[44px] font-bold md:text-[64px] xl:max-w-none xl:text-[78px]">
                GPUs across India, rented by the second.
              </h1>
              <p className="mt-[26px] max-w-[520px] text-[17px] leading-[1.5] text-soft md:text-xl">
                Meridian is a marketplace and control plane for GPU compute. Rent from data centres across India, list the GPUs you own,
                and run training jobs and model servers on either. Billed in rupees.
              </p>
              <div className="mt-9 flex flex-col gap-3.5 sm:flex-row">
                <Button asChild className={cn(BTN, "justify-center")}>
                  <a href="/ui/marketplace">Browse GPUs</a>
                </Button>
                <Button asChild variant="outline" className={cn(BTN, GHOST, "justify-center")}>
                  <a href="/ui/marketplace?tab=listings">List your GPUs</a>
                </Button>
              </div>
              <p className="mt-5 text-[15px] text-soft">
                Running your own servers?{" "}
                <Link to="/install" className="font-semibold text-link underline underline-offset-4">
                  Install the gateway
                </Link>
              </p>
            </div>
            <ListingBoard />
          </section>

          <Section id="rent" label="rent" className="pb-[72px] md:pb-[104px]">
            <h2 id="rent-h" className={cn(H2, "max-w-[760px]")}>
              Rent it, list it, or run what you already own.
            </h2>
            <div className="mt-11 grid gap-5 lg:grid-cols-[1.25fr_1fr_1fr]">
              {WAYS.map((w) => (
                <div
                  key={w.title}
                  className={cn(
                    "flex flex-col rounded-[20px] border-[1.5px] p-[30px_30px_28px] lg:min-h-[330px]",
                    w.lead ? "border-cobalt bg-cobalt text-white [--ring:#ffffff]" : "border-border bg-card",
                  )}
                >
                  <h3 className="site-display text-[30px]">{w.title}</h3>
                  <p className={cn("mt-3.5 text-[16.5px]", w.lead ? "text-[#dce2ff]" : "text-soft")}>{w.body}</p>
                  <ul className="mt-[18px] text-[15.5px]">
                    {w.points.map((p) => (
                      <li key={p} className={cn("border-t py-[9px]", w.lead ? "border-[#4c68f6]" : "border-mist")}>
                        {p}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-auto pt-[22px]">
                    <a href={w.href} className="text-base font-semibold underline underline-offset-[5px]">
                      {w.cta}
                    </a>
                  </div>
                </div>
              ))}
            </div>
          </Section>
        </div>

        <Section id="pricing" label="pricing" className="bg-band py-[72px] text-white [--ring:#8da0ff] md:py-[104px]">
          <div className={cn(WRAP, "grid items-center gap-9 lg:grid-cols-[minmax(0,600fr)_minmax(0,520fr)] lg:gap-12 xl:gap-20")}>
            <div>
              <h2 id="pricing-h" className="site-display text-[38px] md:text-[60px]">
                The provider's price, plus 2%.
              </h2>
              <p className="mt-[22px] max-w-[540px] text-[19px] text-night-soft">
                Each listing shows a price per GPU-hour set by the data centre that owns the hardware. You pay that, for the seconds you
                hold it, plus a 2% Meridian fee.
              </p>
              <dl className="mt-[34px] grid gap-x-8 sm:grid-cols-2">
                {FACTS.map((f) => (
                  <div key={f.title} className="border-t border-night-line py-[18px] text-base text-night-soft">
                    <dt className="mb-[3px] text-[17px] font-semibold text-white">{f.title}</dt>
                    <dd>{f.body}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="rounded-[20px] bg-card p-[26px_22px] text-card-foreground sm:p-[34px_36px]">
              <h3 className="font-display text-[22px] font-semibold">
                {EXAMPLE.gpus} GPUs for {EXAMPLE.seconds / 60} minutes
              </h3>
              <p className="mt-1 text-[14.5px] text-soft">Listed at {formatINR(EXAMPLE.ratePaisePerGpuHour)} per GPU-hour</p>
              <dl className="mt-[22px] tabular-nums">
                <div className="flex justify-between gap-4 border-t border-mist py-3.5 text-[16.5px]">
                  <dt className="text-soft">
                    {EXAMPLE.gpus} GPUs × {formatINR(EXAMPLE.ratePaisePerGpuHour)} × {hours} hour
                  </dt>
                  <dd>{formatINR(r.subtotal)}</dd>
                </div>
                <div className="flex justify-between gap-4 border-t border-mist py-3.5 text-[16.5px]">
                  <dt className="text-soft">Meridian fee, {EXAMPLE.feePercent}%</dt>
                  <dd>{formatINR(r.fee)}</dd>
                </div>
                <div className="flex items-baseline justify-between gap-4 border-t-2 border-foreground py-3.5 text-xl font-bold">
                  <dt>You pay</dt>
                  <dd className="font-display text-[30px] tracking-[-0.01em]">{formatINR(r.total)}</dd>
                </div>
                <div className="mt-[18px] flex justify-between gap-4 rounded-xl bg-payout-bg px-4 py-3.5 text-[15px] font-semibold text-payout-fg">
                  <dt>The provider receives</dt>
                  <dd>{formatINR(r.providerReceives)}</dd>
                </div>
              </dl>
            </div>
          </div>
        </Section>

        <div className={WRAP}>
          <Section id="jobs" label="jobs" className="py-[72px] md:py-[104px]">
            <div className="grid items-center gap-9 lg:grid-cols-[minmax(0,520fr)_minmax(0,620fr)] lg:gap-[60px]">
              <div>
                <h2 id="jobs-h" className={H2}>
                  One command from image to result.
                </h2>
                <p className="mt-5 text-[19px] text-soft">
                  A job is a container pinned to an exact image. Run it on your own nodes or rent a listing for exactly as long as it
                  takes.
                </p>
                <dl className="mt-[30px]">
                  {JOB_POINTS.map((j) => (
                    <div key={j.title} className="border-t-[1.5px] border-border py-4 text-[16.5px] text-soft">
                      <dt className="mb-0.5 text-[17.5px] font-semibold text-foreground">{j.title}</dt>
                      <dd>{j.body}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div className="rounded-[20px] bg-band pb-[26px] font-mono text-[12.5px] leading-[1.75] text-[#dde3ff] shadow-[0_30px_60px_-30px_rgba(12,18,48,0.5)] sm:text-[14.5px]">
                <div className="flex h-11 items-center gap-2 border-b border-night-line px-5 font-sans text-[13.5px] text-night-soft">
                  <i aria-hidden className="size-[11px] rounded-full bg-night-line" />
                  <i aria-hidden className="size-[11px] rounded-full bg-night-line" />
                  <i aria-hidden className="size-[11px] rounded-full bg-night-line" />
                  <span className="ml-2.5">meridian compute</span>
                </div>
                <pre translate="no" className="px-[18px] pt-[18px] whitespace-pre-wrap [overflow-wrap:anywhere] sm:px-[26px] sm:pt-[22px]">
                  <span className="text-term-dim">$</span> <span className="text-white">meridian compute run --offer off_b0ae --gpus 2 --follow \</span>
                  {"\n    "}
                  <span className="text-white">--image docker.io/pytorch/pytorch@sha256:d9e8… \</span>
                  {"\n    "}
                  <span className="text-white">-- python train.py --epochs 3</span>
                  {"\n\n"}
                  submitted job_a4f6 <span className="text-term-dim">(scheduled)</span>
                  {"\n"}
                  <span className="text-term-amb">starting</span>
                  {"   pulling image on gpu-host-a\n"}
                  <span className="text-term-ok">running</span>
                  {"    2 × H100, Mumbai\nepoch 3/3  loss 0.12\n"}
                  <span className="text-term-ok">succeeded</span>
                  {"  exit 0\n\n"}
                  <span className="text-term-dim">$</span> <span className="text-white">meridian compute rentals</span>
                  {"\nrnt_290c  2 × H100  ended (job finished)  "}
                  <span className="text-term-amb">₹122.40</span>
                </pre>
              </div>
            </div>
          </Section>

          <Section id="gateway" label="gateway" className="pb-[72px] md:pb-[104px]">
            <div className="rounded-[20px] border-[1.5px] border-border bg-card px-5 py-7 sm:rounded-3xl sm:p-14">
              <h2 id="gateway-h" className={H2}>
                One endpoint for every GPU you run.
              </h2>
              <p className="mt-5 max-w-[680px] text-[19px] text-soft">
                The Meridian gateway sits in front of the model servers you host: vLLM, SGLang, Ollama or anything that speaks the OpenAI
                API. Point your apps at it once.
              </p>
              <div className="mt-12 grid items-center gap-3.5 lg:grid-cols-[minmax(0,230px)_minmax(32px,1fr)_minmax(0,250px)_minmax(32px,1fr)_minmax(0,250px)] lg:gap-0">
                <Node title="Your apps" sub="OpenAI-compatible clients" />
                <Wire />
                <Node core title="Meridian gateway" sub="Routes, retries, records" />
                <Wire />
                <div className="grid gap-3">
                  <Node title="vLLM" sub="H100, Mumbai" />
                  <Node title="SGLang" sub="A100, Hyderabad" />
                  <Node title="Ollama" sub="RTX 4090, office" />
                </div>
              </div>
              <div className="mt-[52px] grid gap-7 sm:grid-cols-2 lg:grid-cols-4">
                {CAPABILITIES.map((c) => (
                  <div key={c.title} className="border-t-2 border-foreground pt-4">
                    <h3 className="font-display text-xl leading-[1.15] font-semibold tracking-[-0.01em]">{c.title}</h3>
                    <p className="mt-2.5 text-[15.5px] text-soft">{c.body}</p>
                  </div>
                ))}
              </div>
              <div className="mt-12 flex flex-col gap-4 sm:flex-row sm:items-center">
                <CopyCommand command={command} className="flex-1" />
                <Button asChild variant="outline" className={cn(BTN, GHOST, "justify-center")}>
                  <Link to="/install">Choose options first</Link>
                </Button>
              </div>
            </div>
          </Section>

          <Section label="privacy" className="pb-[72px] md:pb-[104px]">
            <h2 id="privacy-h" className={H2}>
              What stays yours.
            </h2>
            <div className="mt-11 grid gap-5 md:grid-cols-3">
              {PRIVACY.map((p) => (
                <div key={p.title} className="rounded-[20px] bg-mist p-6 lg:p-[30px]">
                  <h3 className="font-display text-2xl leading-[1.1] font-semibold tracking-[-0.015em]">{p.title}</h3>
                  <p className="mt-3 text-base text-soft">{p.body}</p>
                </div>
              ))}
            </div>
          </Section>

          <Section label="faq" className="pb-[72px] md:pb-[104px]">
            <div className="grid gap-9 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:gap-20">
              <h2 id="faq-h" className={H2}>
                Questions before you start.
              </h2>
              <div>
                {FAQ.map((f) => (
                  <div key={f.q} className="border-t-[1.5px] border-border py-6">
                    <h3 className="text-[19px] font-semibold">{f.q}</h3>
                    <p className="mt-2 max-w-[640px] text-[16.5px] text-soft">{f.a}</p>
                  </div>
                ))}
              </div>
            </div>
          </Section>
        </div>

        <Section label="close" className="bg-cobalt py-16 text-white [--ring:#ffffff] md:py-[88px]">
          <div className={cn(WRAP, "flex flex-col items-start justify-between gap-7 md:flex-row md:items-center")}>
            <div>
              <h2 id="close-h" className="site-display max-w-[700px] text-[38px] md:text-[60px]">
                See what is available right now.
              </h2>
              <p className="mt-4 text-[19px] text-[#dce2ff]">Sign in with Google or an emailed code. No card needed to look.</p>
            </div>
            <div className="flex w-full flex-col gap-3.5 sm:flex-row md:w-auto">
              <Button asChild className={cn(BTN, ON_COBALT_PRIMARY, "justify-center")}>
                <a href="/ui/marketplace">Browse GPUs</a>
              </Button>
              <Button asChild variant="outline" className={cn(BTN, ON_COBALT_GHOST, "justify-center")}>
                <Link to="/install">Install the gateway</Link>
              </Button>
            </div>
          </div>
        </Section>
      </main>
      <SiteFooter />
    </div>
  )
}
