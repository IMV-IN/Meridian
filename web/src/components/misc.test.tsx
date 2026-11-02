import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { SettingsProvider } from "@/providers/settings-provider"

import { ConfirmDialog } from "./misc"
import { StatusBadge, httpStatusTone } from "./status-badge"

describe("ConfirmDialog", () => {
  it("keeps destructive actions disabled until the exact text is typed", async () => {
    const onConfirm = vi.fn<() => void>()
    render(
      <SettingsProvider>
        <ConfirmDialog
          open
          onOpenChange={() => undefined}
          title="Delete acme-search?"
          description="Requests will fail with 401."
          confirmLabel="Delete key"
          destructive
          typeToConfirm="acme-search"
          onConfirm={onConfirm}
        />
      </SettingsProvider>,
    )
    const button = screen.getByRole("button", { name: "Delete key" })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/to confirm/), "acme-searc")
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/to confirm/), "h")
    expect(button).toBeEnabled()
    await userEvent.click(button)
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})

describe("StatusBadge", () => {
  it("always renders a text label (status is never color-only)", () => {
    render(<StatusBadge tone="critical">Unhealthy</StatusBadge>)
    expect(screen.getByText("Unhealthy")).toBeInTheDocument()
  })

  it("maps HTTP codes to status tones", () => {
    expect(httpStatusTone(200)).toBe("good")
    expect(httpStatusTone(429)).toBe("warning")
    expect(httpStatusTone(403)).toBe("serious")
    expect(httpStatusTone(499)).toBe("critical")
    expect(httpStatusTone(503)).toBe("critical")
  })
})
