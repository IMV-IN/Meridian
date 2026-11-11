import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { EarningsEstimator, RentalCalculator } from "./calculators"

describe("RentalCalculator", () => {
  it("starts at 2 GPUs for half an hour at ₹120: ₹120.00 plus a ₹2.40 fee", () => {
    render(<RentalCalculator />)
    expect(screen.getByText("₹120.00")).toBeInTheDocument()
    expect(screen.getByText("₹2.40")).toBeInTheDocument()
    expect(screen.getByText("₹122.40")).toBeInTheDocument()
  })

  it("starts from a listing's GPU count and price", () => {
    render(<RentalCalculator preset={{ gpus: 4, ratePaise: 18500, nonce: 1 }} />)
    expect(screen.getByLabelText("GPUs")).toHaveValue("4")
    expect(screen.getByLabelText("Price per GPU-hour")).toHaveValue("185")
    expect(screen.getByText("₹370.00")).toBeInTheDocument() // 4 x 0.5 h x ₹185
  })

  it("flags a bad input and keeps the last good quote", async () => {
    render(<RentalCalculator />)
    const gpus = screen.getByLabelText("GPUs")
    await userEvent.clear(gpus)
    await userEvent.type(gpus, "0")
    expect(gpus).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByRole("alert")).not.toBeEmptyDOMElement()
    expect(screen.getByText("₹122.40")).toBeInTheDocument()
  })
})

describe("EarningsEstimator", () => {
  it("estimates a day and thirty days of rentals", () => {
    render(<EarningsEstimator />)
    expect(screen.getByText("₹4,800.00")).toBeInTheDocument() // 4 GPUs x ₹150 x 8 h
    expect(screen.getByText("₹1,44,000")).toBeInTheDocument()
  })
})
