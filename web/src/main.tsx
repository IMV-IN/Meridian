import "./index.css"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AuthProvider } from "@/providers/auth-provider"
import { SettingsProvider } from "@/providers/settings-provider"
import { ThemeProvider } from "@/providers/theme-provider"

import { createRouter } from "./routes"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1_000,
      refetchOnWindowFocus: false,
      // Live views pause in background tabs; no point polling hidden pages.
      refetchIntervalInBackground: false,
    },
  },
})

const router = createRouter()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <SettingsProvider>
          <AuthProvider>
            <TooltipProvider delayDuration={250}>
              <RouterProvider router={router} />
              <Toaster position="bottom-right" closeButton />
            </TooltipProvider>
          </AuthProvider>
        </SettingsProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
