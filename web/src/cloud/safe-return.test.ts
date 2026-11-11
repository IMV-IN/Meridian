import { describe, expect, it } from "vitest"

import { safeReturnPath } from "./safe-return"

const ORIGIN = "https://cloud.example"

describe("safeReturnPath (M4: open redirect after sign-in)", () => {
  it("keeps same-origin paths with their query and fragment", () => {
    expect(safeReturnPath("/oauth/gateway/authorize?state=abc", ORIGIN)).toBe("/oauth/gateway/authorize?state=abc")
    expect(safeReturnPath("/ui/jobs#top", ORIGIN)).toBe("/ui/jobs#top")
    // Percent-encoded TAB stays encoded in a path, so it is still this origin.
    expect(safeReturnPath("/%09/evil.com", ORIGIN)).toBe("/%09/evil.com")
  })

  it.each([
    "/\t/evil.com",
    "/\n/evil.com",
    "/\r/evil.com",
    "/\t\t/evil.com",
    "/\\evil.com",
    "//evil.com",
    "https://evil.com/",
    "javascript:alert(1)",
    "ui/relative",
    "/\u0085/evil.com",
    "",
  ])("refuses %j", (value) => {
    expect(safeReturnPath(value, ORIGIN)).toBeNull()
  })

  it("refuses a missing value", () => {
    expect(safeReturnPath(null, ORIGIN)).toBeNull()
  })
})
