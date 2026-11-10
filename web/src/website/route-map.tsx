/**
 * The hero diagram: requests leave three apps, cross the gateway (the
 * meridian line) and land on healthy model servers. gpu-b is failing its
 * health checks, so no route reaches it. The amber dots are the page's only
 * ambient motion, and they're hidden for reduced-motion users.
 */

const W = 760
const H = 300
const GATE = W / 2
const APPS = [
  { y: 58, label: "Chat app" },
  { y: 150, label: "Support agent" },
  { y: 242, label: "Nightly batch" },
]
const SERVERS = [
  { y: 58, label: "gpu-a", sub: "vLLM, Llama 3.1 70B", healthy: true },
  { y: 150, label: "gpu-b", sub: "failing health checks", healthy: false },
  { y: 242, label: "laptop", sub: "Ollama, Qwen 2.5", healthy: true },
]
const APP_X = 150
const SERVER_X = W - 170

function route(from: number, to: number) {
  const a = APPS[from].y
  const b = SERVERS[to].y
  const mid = (a + b) / 2
  return `M${APP_X},${a} C${APP_X + 120},${a} ${GATE - 90},${mid} ${GATE},${mid} C${GATE + 90},${mid} ${SERVER_X - 120},${b} ${SERVER_X},${b}`
}

// (app, server, start offset in seconds): only healthy servers receive traffic.
const TRAFFIC: [number, number, number][] = [
  [0, 0, 0],
  [1, 2, 0.6],
  [2, 2, 1.3],
  [0, 2, 1.9],
  [1, 0, 2.5],
  [2, 0, 3.1],
  [0, 0, 3.7],
]
const ROUTES = [...new Set(TRAFFIC.map(([a, s]) => `${a}-${s}`))].map((k) => k.split("-").map(Number) as [number, number])

export function RouteMap() {
  return (
    <figure className="site-map">
      {/* Narrow screens scroll the diagram sideways rather than shrinking its labels. */}
      <div
        tabIndex={0}
        role="region"
        aria-label="Routing diagram (scrolls sideways on small screens)"
        className="-mx-4 overflow-x-auto rounded-md px-4 focus-visible:outline-2 focus-visible:outline-ring sm:mx-0 sm:px-0"
      >
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full min-w-[620px]" role="img" aria-labelledby="route-map-title route-map-desc">
        <title id="route-map-title">How Meridian routes requests</title>
        <desc id="route-map-desc">
          Requests from a chat app, a support agent and a nightly batch job pass through the Meridian gateway and go to gpu-a and a
          laptop running Ollama. gpu-b is failing its health checks, so the gateway sends it nothing.
        </desc>

        {/* routes in use */}
        {ROUTES.map(([a, s]) => (
          <path key={`${a}-${s}`} id={`r-${a}-${s}`} d={route(a, s)} fill="none" stroke="var(--contour)" strokeWidth={1.25} />
        ))}
        {/* the route the gateway refuses */}
        <path d={route(1, 1)} fill="none" stroke="var(--contour)" strokeWidth={1.25} strokeDasharray="3 6" opacity={0.8} />

        {/* the meridian */}
        <line x1={GATE} y1={8} x2={GATE} y2={H - 8} stroke="var(--ink)" strokeWidth={2} />
        <line x1={GATE - 6} y1={8} x2={GATE + 6} y2={8} stroke="var(--ink)" strokeWidth={2} />
        <line x1={GATE - 6} y1={H - 8} x2={GATE + 6} y2={H - 8} stroke="var(--ink)" strokeWidth={2} />
        <rect x={GATE - 44} y={H / 2 - 13} width={88} height={26} rx={4} fill="var(--ink)" />
        <text x={GATE} y={H / 2 + 5} textAnchor="middle" className="fill-paper" fontSize={13} fontWeight={600}>
          Meridian
        </text>

        {APPS.map((a) => (
          <g key={a.label}>
            <circle cx={APP_X} cy={a.y} r={5} fill="var(--paper)" stroke="var(--ink)" strokeWidth={1.75} />
            <text x={APP_X - 14} y={a.y + 5} textAnchor="end" fill="var(--ink)" fontSize={14}>
              {a.label}
            </text>
          </g>
        ))}

        {SERVERS.map((s) => (
          <g key={s.label}>
            <rect
              x={SERVER_X - 6}
              y={s.y - 6}
              width={12}
              height={12}
              rx={2}
              fill={s.healthy ? "var(--ink)" : "var(--paper)"}
              stroke={s.healthy ? "var(--ink)" : "var(--deep)"}
              strokeWidth={1.75}
              strokeDasharray={s.healthy ? undefined : "2 2"}
            />
            <text x={SERVER_X + 16} y={s.y} fill="var(--ink)" fontSize={14} fontWeight={600} opacity={s.healthy ? 1 : 0.7}>
              {s.label}
            </text>
            <text x={SERVER_X + 16} y={s.y + 17} fill="var(--deep)" fontSize={12}>
              {s.sub}
            </text>
          </g>
        ))}

        {TRAFFIC.map(([a, s, begin], i) => (
          <circle key={i} r={4} className="request" opacity={0}>
            <animateMotion dur="4.2s" begin={`${begin}s`} repeatCount="indefinite" keyPoints="0;1" keyTimes="0;1" calcMode="spline" keySplines="0.4 0 0.3 1">
              <mpath href={`#r-${a}-${s}`} />
            </animateMotion>
            <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.08;0.9;1" dur="4.2s" begin={`${begin}s`} repeatCount="indefinite" />
          </circle>
        ))}
      </svg>
      </div>
      <figcaption className="mt-3 max-w-2xl text-sm text-muted-foreground">
        Apps call one endpoint. The gateway picks a healthy server for each request; gpu-b is failing its health checks, so it gets
        nothing until it recovers.
      </figcaption>
    </figure>
  )
}
