import { describe, expect, it } from "vitest"

import { COMPUTE_GUIDE, DOCS, QUICKSTART } from "./site-lib"

describe("docs links", () => {
  it("point at the control plane's own /docs/ site, not GitHub", () => {
    expect([DOCS, COMPUTE_GUIDE, QUICKSTART]).toEqual(["/docs/", "/docs/COMPUTE/", "/docs/QUICKSTART/"])
  })
})
