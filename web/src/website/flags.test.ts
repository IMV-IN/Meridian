import { describe, expect, it } from "vitest"

import { DEFAULTS, installCommand, problems, questions, shellQuote, toFlags, type InstallOptions } from "./flags"

const ORIGIN = "https://uno-q.example.ts.net"

describe("install flags", () => {
  it("emits only what differs from the defaults, plus cloud and service", () => {
    expect(toFlags(DEFAULTS, ORIGIN)).toEqual([`--cloud ${ORIGIN}`, "--service user"])
    expect(installCommand(DEFAULTS, ORIGIN)).toBe(
      `curl -fsSL ${ORIGIN}/install.sh | bash -s -- \\\n  --cloud ${ORIGIN} \\\n  --service user`,
    )
    expect(installCommand({ ...DEFAULTS, cloud: false, service: "none" }, ORIGIN)).toBe(
      `curl -fsSL ${ORIGIN}/install.sh | bash -s -- --service none`,
    )
  })

  it("maps every option to a meridian init flag", () => {
    const o: InstallOptions = {
      ...DEFAULTS,
      service: "system",
      port: "9090",
      listen: "all",
      detect: false,
      backends: [{ url: "http://10.0.0.5:8000/", engine: "vllm", model: "meta-llama/Llama-3.1-8B" }],
      strategy: "token_aware",
      healthInterval: "10",
      retries: "2",
      circuitBreaker: true,
      sessionAffinity: true,
      tiering: true,
      auth: "on",
      gatewayName: "Lab GPU box",
      consoleUrl: "http://gw.local:9090/",
      keySignIn: false,
      rateLimit: "5",
      rateBurst: "20",
      budgets: [{ org: "acme", tokens: "2000000" }],
      pii: "redact_for_logs",
      cost: true,
      currency: "EUR",
      auditLog: false,
    }
    expect(toFlags(o, ORIGIN)).toEqual([
      "--port 9090",
      "--listen all",
      "--backend http://10.0.0.5:8000,engine=vllm,model=meta-llama/Llama-3.1-8B",
      "--no-detect",
      "--strategy token_aware",
      "--health-interval 10",
      "--retries 2",
      "--circuit-breaker",
      "--session-affinity",
      "--tiering",
      "--rate-limit 5",
      "--rate-burst 20",
      "--budget acme=2000000",
      "--pii redact_for_logs",
      "--cost",
      "--currency EUR",
      "--no-audit-log",
      "--auth",
      `--cloud ${ORIGIN}`,
      "--gateway-name 'Lab GPU box'",
      "--console-url http://gw.local:9090",
      "--no-key-sign-in",
      "--service system",
    ])
    expect(installCommand(o, ORIGIN).split("\n")).toHaveLength(24)
    expect(questions(o)).toEqual(["Your Meridian Cloud gateway token (hidden as you paste it)"])
  })

  it("quotes for the shell only when needed, and safely", () => {
    expect(shellQuote("http://a:1/x")).toBe("http://a:1/x")
    expect(shellQuote("a b")).toBe("'a b'")
    expect(shellQuote("it's $(rm -rf ~)")).toBe(`'it'\\''s $(rm -rf ~)'`)
    expect(shellQuote("")).toBe("''")
  })

  it("leaves invalid fields out of the command and reports them", () => {
    const o: InstallOptions = {
      ...DEFAULTS,
      port: "99999",
      backends: [{ url: "not a url; rm -rf /", engine: "vllm", model: "" }],
      budgets: [{ org: "bad org", tokens: "x" }],
      gatewayName: "$(whoami)",
    }
    const flags = toFlags(o, ORIGIN).join(" ")
    expect(flags).not.toContain("--port")
    expect(flags).not.toContain("--backend")
    expect(flags).not.toContain("--budget")
    expect(flags).not.toContain("whoami")
    expect(problems(o).map((p) => p.field).sort()).toEqual(["backend-0", "budget-org-0", "budget-tokens-0", "gatewayName", "port"])
  })
})
