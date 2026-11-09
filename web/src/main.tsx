import "./index.css"

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query"
import { StrictMode, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { makeQueryClient } from "@/lib/query-client"
import { AuthProvider } from "@/providers/auth-provider"
import { SettingsProvider } from "@/providers/settings-provider"
import { ThemeProvider } from "@/providers/theme-provider"

import { detectCloud, readBootInfo, type CloudInfo } from "./cloud/api"
import { createRouter } from "./routes"

function Frame({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <StrictMode>
      <ThemeProvider>
        <QueryClientProvider client={client}>
          <SettingsProvider>
            <TooltipProvider delayDuration={250}>
              {children}
              <Toaster position="bottom-right" closeButton />
            </TooltipProvider>
          </SettingsProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </StrictMode>
  )
}

async function boot() {
  const root = createRoot(document.getElementById("root")!)
  // Production: the page says which console it is (no request before first
  // render). Vite's dev server serves a plain page, so probe there instead.
  const embedded = readBootInfo()
  const info: CloudInfo | null = embedded !== undefined ? embedded : import.meta.env.DEV ? await detectCloud() : null
  if (info) {
    // Meridian Cloud: accounts + workspaces; loaded on demand so the
    // self-hosted console bundle stays unchanged.
    const [{ CloudSessionProvider }, { createCloudRouter }] = await Promise.all([import("./cloud/session"), import("./cloud/app")])
    root.render(
      <Frame client={makeQueryClient({ cloud: true })}>
        <CloudSessionProvider info={info}>
          <RouterProvider router={createCloudRouter()} />
        </CloudSessionProvider>
      </Frame>,
    )
    return
  }
  root.render(
    <Frame client={makeQueryClient()}>
      <AuthProvider>
        <RouterProvider router={createRouter()} />
      </AuthProvider>
    </Frame>,
  )
}

void boot()
