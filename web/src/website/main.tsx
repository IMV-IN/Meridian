import "./site.css"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { createBrowserRouter, RouterProvider } from "react-router"

import { InstallPage } from "./install"
import { LabsPage } from "./labs"
import { LandingPage } from "./landing"
import { TeachingPage } from "./teaching"

// The public feed is cheap and read-only: one retry, then show the error state.
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 1500 } } })

const router = createBrowserRouter([
  { path: "/", element: <LandingPage /> },
  { path: "/labs", element: <LabsPage /> },
  { path: "/teaching", element: <TeachingPage /> },
  { path: "/install", element: <InstallPage /> },
  { path: "*", element: <LandingPage /> },
])

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
