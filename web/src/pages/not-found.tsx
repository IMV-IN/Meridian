import { Compass } from "lucide-react"
import { Link, useLocation } from "react-router"

import { Page } from "@/components/page"
import { EmptyState } from "@/components/states"
import { Button } from "@/components/ui/button"

export default function NotFoundPage() {
  const { pathname } = useLocation()
  return (
    <Page className="max-w-3xl pt-16">
      <EmptyState icon={Compass} title="Page not found" description={<>Nothing lives at <code className="text-xs">{pathname}</code> in the console.</>}>
        <Button size="sm" asChild>
          <Link to="/">Back to overview</Link>
        </Button>
      </EmptyState>
    </Page>
  )
}
