import "./site.css"

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { createBrowserRouter, RouterProvider } from "react-router"

import { TooltipProvider } from "@/components/ui/tooltip"

import { InstallPage } from "./install"
import { LandingPage } from "./landing"

// Follow the system theme (the site has no theme switch).
const dark = window.matchMedia("(prefers-color-scheme: dark)")
const applyTheme = () => document.documentElement.classList.toggle("dark", dark.matches)
applyTheme()
dark.addEventListener("change", applyTheme)

const router = createBrowserRouter([
  { path: "/", element: <LandingPage /> },
  { path: "/install", element: <InstallPage /> },
  { path: "*", element: <LandingPage /> },
])

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider delayDuration={250}>
      <RouterProvider router={router} />
    </TooltipProvider>
  </StrictMode>,
)
