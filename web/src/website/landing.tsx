import { Link } from "react-router"

import { MeridianMark } from "@/components/layout/logo"
import { Button } from "@/components/ui/button"

import { CopyCommand } from "./copy-command"
import { RouteMap } from "./route-map"

const REPO = "https://github.com/IMV-IN/Meridian"

const CAPABILITIES = [
  {
    title: "Picks a server for every request",
    body: "By load, recent latency or the size of the request. Long prompts and long answers can go to separate pools, and a conversation stays on the server that already has its context cached.",
  },
  {
    title: "Keeps answering when a server fails",
    body: "Health checks and circuit breakers take a failing server out of rotation, and retries move the request elsewhere. Your apps see one endpoint that stays up.",
  },
  {
    title: "Shows what every request did",
    body: "A console with live traffic, latency percentiles, the request log and Prometheus metrics. It records which server answered and how long it took, never the prompt.",
  },
  {
    title: "Controls who uses what",
    body: "API keys per app and team, model allow-lists, rate limits, daily token budgets, cost per org and PII redaction, set in one config file or from the console.",
  },
]

const STEPS = [
  {
    title: "Install a gateway next to your model servers",
    body: "Run the command on a laptop, a server or a GPU box. It finds Ollama, vLLM, SGLang or LM Studio on that machine and asks about anything else it needs.",
  },
  {
    title: "Connect it to your workspace",
    body: "Create a gateway token in Meridian Cloud and paste it when the installer asks. Your team then signs in to the gateway's console with their Meridian Cloud accounts.",
  },
  {
    title: "Add GPU machines when you need more",
    body: "meridian-node runs on each GPU host and starts the inference servers you assign here. Their endpoints show up as routes on your gateways automatically.",
  },
]

export function SiteHeader() {
  return (
    <header className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-5 sm:px-8">
      <Link to="/" className="flex items-center gap-2.5 rounded-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
        <MeridianMark className="size-8" />
        <span className="text-base font-semibold tracking-tight">Meridian</span>
      </Link>
      <nav aria-label="Site" className="flex items-center gap-1 text-sm sm:gap-2">
        <Button variant="ghost" size="sm" asChild className="hidden sm:inline-flex">
          <a href="/#how-it-works">How it works</a>
        </Button>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/install">Install</Link>
        </Button>
        <Button size="sm" asChild>
          <a href="/ui/">Sign in</a>
        </Button>
      </nav>
    </header>
  )
}

export function SiteFooter() {
  return (
    <footer className="mx-auto w-full max-w-6xl px-4 pt-6 pb-12 sm:px-8">
      <div className="site-rule" />
      <div className="mt-6 flex flex-wrap items-center justify-between gap-4 text-sm text-muted-foreground">
        <p>Meridian is open source. This instance runs on hardware its owner controls.</p>
        <div className="flex gap-5">
          <a className="underline-offset-4 hover:underline" href={REPO}>
            Source on GitHub
          </a>
          <a className="underline-offset-4 hover:underline" href={`${REPO}/tree/main/docs`}>
            Documentation
          </a>
        </div>
      </div>
    </footer>
  )
}

export function LandingPage() {
  const command = `curl -fsSL ${window.location.origin}/install.sh | bash`
  return (
    <div className="min-h-svh">
      <SiteHeader />
      <main>
        <section className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-8 sm:pt-16">
          <h1 className="site-display max-w-[15ch] text-[clamp(2.75rem,8vw,6.25rem)]">One endpoint for every GPU you run.</h1>
          <p className="mt-6 max-w-[60ch] text-lg leading-relaxed text-deep">
            Meridian is an OpenAI-compatible gateway for model servers you host yourself: vLLM, SGLang, Ollama or anything that speaks the
            OpenAI API. Point your apps at it once. It sends each request to the server best placed to answer, routes around failures, and
            shows you what happened. Prompts never leave your network.
          </p>
          <div className="mt-9 max-w-2xl">
            <h2 className="mb-2 text-sm font-medium">Install a gateway on Linux or macOS</h2>
            <CopyCommand command={command} />
            <p className="mt-3 text-sm text-muted-foreground">
              It asks for anything it needs as it goes.{" "}
              <Link to="/install" className="font-medium text-foreground underline underline-offset-4">
                Choose routing, keys and limits first
              </Link>
              , or{" "}
              <a href="/install.sh" className="underline underline-offset-4">
                read the script
              </a>
              .
            </p>
          </div>
        </section>

        <section aria-label="Routing diagram" className="mx-auto max-w-6xl px-4 pb-20 sm:px-8">
          <RouteMap />
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-8">
          <h2 className="site-display text-4xl sm:text-5xl">What the gateway does</h2>
          <dl className="mt-10 grid gap-x-12 md:grid-cols-2">
            {CAPABILITIES.map((c) => (
              <div key={c.title} className="border-t border-contour py-6">
                <dt className="text-lg font-semibold">{c.title}</dt>
                <dd className="mt-2 max-w-[56ch] leading-relaxed text-deep">{c.body}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section id="how-it-works" className="scroll-mt-6 bg-ink text-paper dark:bg-card dark:text-foreground">
          <div className="mx-auto grid max-w-6xl gap-12 px-4 py-20 sm:px-8 lg:grid-cols-[1fr_1.4fr]">
            <div>
              <h2 className="site-display text-4xl sm:text-5xl">How it fits together</h2>
              <p className="mt-5 max-w-[42ch] leading-relaxed opacity-85">
                Gateways run in your network and do the routing. Meridian Cloud, this site, is where your team signs in, sees every gateway
                and manages GPU machines.
              </p>
            </div>
            <ol className="space-y-8">
              {STEPS.map((s, i) => (
                <li key={s.title} className="grid grid-cols-[2.5rem_1fr] gap-x-3">
                  <span aria-hidden className="site-display text-3xl text-signal">
                    {i + 1}
                  </span>
                  <div>
                    <h3 className="text-lg font-semibold">{s.title}</h3>
                    <p className="mt-1.5 max-w-[58ch] leading-relaxed opacity-85">{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-10 px-4 py-20 sm:px-8 md:grid-cols-2">
          <div>
            <h2 className="site-display text-4xl sm:text-5xl">What reaches Meridian Cloud</h2>
            <p className="mt-5 max-w-[48ch] leading-relaxed text-deep">
              Every 15 seconds each gateway reports how it's doing, so you can see your whole fleet in one place. That report is metadata.
            </p>
          </div>
          <div className="grid gap-8 sm:grid-cols-2">
            <div>
              <h3 className="font-semibold">Sent</h3>
              <ul className="mt-3 space-y-2 leading-relaxed text-deep">
                <li>Server health and circuit state</li>
                <li>Request counts and latency percentiles</li>
                <li>Request IDs, models, status codes</li>
                <li>Who signed in to which gateway</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold">Never sent</h3>
              <ul className="mt-3 space-y-2 leading-relaxed text-deep">
                <li>Prompts and completions</li>
                <li>API keys and upstream credentials</li>
                <li>Your config files</li>
              </ul>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-24 sm:px-8">
          <div className="site-rule" />
          <div className="flex flex-wrap items-end justify-between gap-6 pt-10">
            <h2 className="site-display max-w-[18ch] text-4xl sm:text-5xl">Set it up the way your fleet runs.</h2>
            <div className="flex flex-wrap gap-3">
              <Button size="lg" asChild>
                <Link to="/install">Configure an install</Link>
              </Button>
              <Button size="lg" variant="outline" asChild>
                <a href="/ui/">Sign in to Meridian Cloud</a>
              </Button>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
