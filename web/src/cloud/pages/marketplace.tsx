import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Cpu, MapPin, Plus, Store } from "lucide-react"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"

import { DataTable, type ColumnDef } from "@/components/data-table"
import { ConfirmDialog, TimeAgo } from "@/components/misc"
import { Page, PageHeader } from "@/components/page"
import { EmptyState, ErrorState, LoadingGrid } from "@/components/states"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { ApiError } from "@/lib/api/client"
import { formatINR, formatSeconds, formatVram, pluralize } from "@/lib/format"

import { atLeast, cloud, rupeesToPaise, type Offer, type Rental } from "../api"
import { endReason, parseSlugs, priceLabel } from "../compute"
import { useBilling, useListableNodes, useOffers, useRentals } from "../hooks"
import { useWorkspace } from "../session"

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.")

function useRefresh() {
  const ws = useWorkspace()
  const qc = useQueryClient()
  return () =>
    Promise.all(
      ["offers", "my-offers", "rentals", "billing", "listable-nodes", "jobs"].map((k) =>
        qc.invalidateQueries({ queryKey: ["cloud", k, ws.id] }),
      ),
    )
}

function gpus(o: { gpu_count: number; gpu_model: string }) {
  return `${o.gpu_count}× ${o.gpu_model}`
}

function OfferCard({ offer, onRent, canRent }: { offer: Offer; onRent: () => void; canRent: boolean }) {
  return (
    <Card className="gap-0 p-0">
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">{offer.title}</h3>
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
              <MapPin className="size-3" aria-hidden /> {offer.region} · {offer.provider.name}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            {offer.available ? (
              <StatusBadge tone="good" size="sm">
                Available
              </StatusBadge>
            ) : (
              <StatusBadge tone="muted" size="sm">
                {offer.unavailable_reason === "rented" ? "Rented" : "Unavailable"}
              </StatusBadge>
            )}
            {offer.visibility === "private" && !offer.mine && (
              <StatusBadge tone="info" size="sm">
                Shared with you
              </StatusBadge>
            )}
          </div>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums">{priceLabel(offer.price_paise_per_gpu_hour)}</span>
          {offer.price_paise_per_gpu_hour > 0 && <span className="text-xs text-muted-foreground">per GPU-hour</span>}
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-muted-foreground">GPUs</dt>
          <dd className="text-right font-medium">{gpus(offer)}</dd>
          <dt className="text-muted-foreground">Memory per GPU</dt>
          <dd className="text-right font-medium">{formatVram(offer.vram_bytes_per_gpu)}</dd>
          <dt className="text-muted-foreground">Whole listing, with {offer.fee_bps / 100}% fee</dt>
          <dd className="text-right font-medium tabular-nums">{offer.hourly_total_paise === 0 ? "Free" : `${formatINR(offer.hourly_total_paise)}/hour`}</dd>
        </dl>
        {offer.description && <p className="line-clamp-2 text-xs text-muted-foreground">{offer.description}</p>}
      </div>
      <div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
        {offer.mine ? (
          <span className="text-xs text-muted-foreground">Your listing</span>
        ) : (
          <>
            {offer.available && canRent ? (
              <Button asChild variant="ghost" size="sm">
                <Link to={`/jobs?new=1&offer=${encodeURIComponent(offer.id)}`}>Run a job on it</Link>
              </Button>
            ) : (
              <span className="text-xs text-muted-foreground">{offer.available ? "Admins can rent" : "Check back later"}</span>
            )}
            <Button size="sm" onClick={onRent} disabled={!offer.available || !canRent}>
              Rent
            </Button>
          </>
        )}
      </div>
    </Card>
  )
}

function RentDialog({ offer, onClose }: { offer: Offer | null; onClose: () => void }) {
  const ws = useWorkspace()
  const refresh = useRefresh()
  const billing = useBilling()
  const rent = useMutation({
    mutationFn: (o: Offer) => cloud.rent(ws.id, o.id),
    onSuccess: async (r) => {
      await refresh()
      toast.success(`Rented ${gpus({ gpu_count: r.gpu_count, gpu_model: r.offer.gpu_model })}`)
      onClose()
    },
  })
  const available = billing.data?.available_paise ?? 0
  const free = offer?.hourly_total_paise === 0
  const short = offer !== null && !free && billing.data !== undefined && available < offer.hourly_total_paise
  return (
    <Dialog
      open={offer !== null}
      onOpenChange={(open) => {
        if (!open) {
          rent.reset()
          onClose()
        }
      }}
    >
      <DialogContent>
        {offer && (
          <>
            <DialogHeader>
              <DialogTitle>Rent {gpus(offer)}</DialogTitle>
              <DialogDescription>
                These GPUs are yours alone until you release them. You are billed per second, whether or not a job is running.
              </DialogDescription>
            </DialogHeader>
            {free ? (
              <p className="text-sm">
                {offer.provider.name} shares this listing with you at no charge. Nothing is billed and no credit is needed.
              </p>
            ) : (
            <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">
                {offer.provider.name}: {offer.gpu_count} × {formatINR(offer.price_paise_per_gpu_hour)}
              </dt>
              <dd className="text-right tabular-nums">{formatINR(offer.hourly_paise)}</dd>
              <dt className="text-muted-foreground">Meridian fee ({offer.fee_bps / 100}%)</dt>
              <dd className="text-right tabular-nums">{formatINR(offer.hourly_fee_paise)}</dd>
              <dt className="border-t pt-1.5 font-medium">Per hour</dt>
              <dd className="border-t pt-1.5 text-right font-medium tabular-nums">{formatINR(offer.hourly_total_paise)}</dd>
              <dt className="text-muted-foreground">Your available credit</dt>
              <dd className="text-right tabular-nums">{billing.data ? formatINR(available) : "…"}</dd>
            </dl>
            )}
            {rent.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {message(rent.error)}
              </p>
            ) : short ? (
              <p role="alert" className="text-sm text-destructive">
                Renting needs one hour of credit up front. Add {formatINR(offer.hourly_total_paise - available)} or more in{" "}
                <Link to="/billing" className="underline underline-offset-4">
                  Billing
                </Link>
                .
              </p>
            ) : free ? null : (
              <p className="text-sm text-muted-foreground">
                The rental ends by itself if your credit runs out, and its jobs stop.
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button onClick={() => rent.mutate(offer)} disabled={rent.isPending || short}>
                {rent.isPending && <Spinner data-icon="inline-start" />}
                {free ? "Rent for free" : `Rent for ${formatINR(offer.hourly_total_paise)}/hour`}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ListNodeDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92svh] overflow-y-auto">
        <ListNodeBody onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function ListNodeBody({ onClose }: { onClose: () => void }) {
  const ws = useWorkspace()
  const refresh = useRefresh()
  const nodes = useListableNodes(true)
  const billing = useBilling()
  const [nodeId, setNodeId] = useState("")
  const [title, setTitle] = useState("")
  const [region, setRegion] = useState("")
  const [price, setPrice] = useState("")
  const [description, setDescription] = useState("")
  const [visibility, setVisibility] = useState<"public" | "private">("public")
  const [sharedWith, setSharedWith] = useState("")
  const slugs = parseSlugs(sharedWith)
  const priv = visibility === "private"
  const minPaise = priv ? 0 : 100
  const listable = (nodes.data ?? []).filter((n) => n.listable)
  const node = listable.find((n) => n.node_id === nodeId)
  const paise = rupeesToPaise(price)
  const fee = billing.data?.fee_bps ?? 200
  const create = useMutation({
    mutationFn: () =>
      cloud.createOffer(ws.id, {
        node_id: nodeId,
        title: title.trim(),
        region: region.trim(),
        description: description.trim(),
        price_paise_per_gpu_hour: paise ?? 0,
        visibility,
        ...(priv ? { shared_with: slugs } : {}),
      }),
    onSuccess: async () => {
      await refresh()
      toast.success("Listed on the marketplace")
      onClose()
    },
  })
  const blocked = (nodes.data ?? []).filter((n) => !n.listable)
  const ready =
    Boolean(node) && title.trim().length >= 3 && region.trim().length >= 2 && paise !== null && paise >= minPaise && (!priv || slugs.length > 0)

  return (
    <>
      <DialogHeader>
        <DialogTitle>List a node's GPUs</DialogTitle>
        <DialogDescription>
          Renters get the node's GPUs exclusively and run their own containers on them. You set the price and can pause the listing at any time.
        </DialogDescription>
      </DialogHeader>
      {nodes.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Loading your nodes…
        </p>
      ) : listable.length === 0 ? (
        <div className="space-y-2 text-sm">
          <p>No node can be listed yet.</p>
          {blocked.length > 0 ? (
            <ul className="space-y-1 text-muted-foreground">
              {blocked.map((n) => (
                <li key={n.node_id}>
                  <span className="font-medium text-foreground">{n.display_name || n.node_id}</span>: {n.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">
              <Link to="/fleet/enrollment" className="underline underline-offset-4">
                Enroll a GPU node
              </Link>{" "}
              first.
            </p>
          )}
          <p className="text-muted-foreground">
            A node must be online and have <code className="font-mono text-[0.85em]">policy.jobs_enabled: true</code> in its agent config.
          </p>
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) create.mutate()
          }}
        >
          <Field>
            <FieldLabel htmlFor="list-node">Node</FieldLabel>
            <Select value={nodeId} onValueChange={setNodeId}>
              <SelectTrigger id="list-node" className="w-full">
                <SelectValue placeholder="Choose a node" />
              </SelectTrigger>
              <SelectContent>
                {listable.map((n) => (
                  <SelectItem key={n.node_id} value={n.node_id}>
                    {n.display_name || n.node_id} · {n.gpu_count}× {n.gpu_model || "GPU"} {formatVram(n.vram_bytes_per_gpu)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="list-title">Title</FieldLabel>
            <Input id="list-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} placeholder="e.g. 4× H100 80 GB, Tier III, Mumbai" />
          </Field>
          <Field>
            <FieldLabel htmlFor="list-visibility">Who can rent it</FieldLabel>
            <Select value={visibility} onValueChange={(v) => setVisibility(v as "public" | "private")}>
              <SelectTrigger id="list-visibility" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="public">Public: anyone on the marketplace</SelectItem>
                <SelectItem value="private">Private: only workspaces you name</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          {priv && (
            <Field>
              <FieldLabel htmlFor="list-share">Share with workspaces</FieldLabel>
              <Input id="list-share" value={sharedWith} onChange={(e) => setSharedWith(e.target.value)} placeholder="partner-university, other-lab" autoComplete="off" spellCheck={false} />
              <FieldDescription>
                Workspace slugs (shown on their Settings page), separated by commas. Only they can see or rent this listing, and you can price it at ₹0 to share it free.
              </FieldDescription>
            </Field>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="list-region">Location</FieldLabel>
              <Input id="list-region" value={region} onChange={(e) => setRegion(e.target.value)} maxLength={80} placeholder="e.g. Mumbai" />
            </Field>
            <Field data-invalid={price !== "" && (paise === null || paise < minPaise)}>
              <FieldLabel htmlFor="list-price">Price per GPU-hour (₹)</FieldLabel>
              <Input id="list-price" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={priv ? "e.g. 0 to share free" : "e.g. 180"} />
              {price !== "" && (paise === null || paise < minPaise) && (
                <FieldError>{priv ? "Enter an amount in rupees, like 0, 180 or 180.50." : "Enter an amount of ₹1 or more, like 180 or 180.50."}</FieldError>
              )}
            </Field>
          </div>
          <Field>
            <FieldLabel htmlFor="list-desc">Description (optional)</FieldLabel>
            <Textarea id="list-desc" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} rows={3} placeholder="Network, storage, uptime, anything a renter should know" />
          </Field>
          {node && paise === 0 && priv && <FieldDescription>Shared free: partners pay nothing and you earn nothing.</FieldDescription>}
          {node && paise !== null && paise >= 100 && (
            <FieldDescription>
              You earn {formatINR(paise * node.gpu_count)} per hour while it is rented. The renter pays that plus Meridian's {fee / 100}% fee.
            </FieldDescription>
          )}
          {create.isError && (
            <p role="alert" className="text-sm text-destructive">
              {message(create.error)}
            </p>
          )}
        </form>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          {listable.length === 0 ? "Close" : "Cancel"}
        </Button>
        {listable.length > 0 && (
          <Button onClick={() => create.mutate()} disabled={!ready || create.isPending}>
            {create.isPending && <Spinner data-icon="inline-start" />}
            List on marketplace
          </Button>
        )}
      </DialogFooter>
    </>
  )
}

function RentalsTable({ canAdmin }: { canAdmin: boolean }) {
  const ws = useWorkspace()
  const refresh = useRefresh()
  const rentals = useRentals()
  const [ending, setEnding] = useState<Rental | null>(null)
  const release = useMutation({
    mutationFn: (r: Rental) => cloud.release(ws.id, r.id),
    onSuccess: async (r) => {
      await refresh()
      toast.success(`Rental ended: ${formatINR(r.amount_paise)} for ${formatSeconds(r.seconds)}`)
      setEnding(null)
    },
    onError: (e) => toast.error(message(e)),
  })
  const columns: ColumnDef<Rental, unknown>[] = [
    {
      id: "what",
      header: "GPUs",
      accessorFn: (r) => r.offer.title,
      cell: ({ row: { original: r } }) => (
        <div className="flex flex-col">
          <span className="font-medium">{gpus({ gpu_count: r.gpu_count, gpu_model: r.offer.gpu_model })}</span>
          <span className="text-xs text-muted-foreground">
            {r.offer.title} · {r.offer.region}
          </span>
        </div>
      ),
    },
    {
      id: "role",
      header: "You are",
      accessorFn: (r) => r.role,
      cell: ({ row: { original: r } }) => (r.role === "consumer" ? `Renting from ${r.counterparty}` : `Renting out to ${r.counterparty}`),
    },
    {
      id: "status",
      header: "Status",
      accessorFn: (r) => r.status,
      cell: ({ row: { original: r } }) =>
        r.status === "active" ? (
          <StatusBadge tone={r.node_online ? "good" : "serious"} size="sm">
            {r.node_online ? "Active" : "Active, node offline"}
          </StatusBadge>
        ) : (
          <StatusBadge tone="muted" size="sm">
            {endReason(r.end_reason)}
          </StatusBadge>
        ),
    },
    { id: "rate", header: "Per hour", meta: { align: "right" }, accessorFn: (r) => r.hourly_total_paise, cell: ({ getValue }) => priceLabel(getValue() as number) },
    { id: "time", header: "Duration", meta: { align: "right" }, accessorFn: (r) => r.seconds, cell: ({ getValue }) => formatSeconds(getValue() as number) },
    {
      id: "amount",
      header: "Amount",
      meta: { align: "right" },
      accessorFn: (r) => r.amount_paise,
      cell: ({ row: { original: r } }) => (
        <span className="tabular-nums" title={r.role === "consumer" ? `Includes ${formatINR(r.fee_paise)} fee` : "Your earnings"}>
          {formatINR(r.role === "consumer" ? -r.amount_paise : r.amount_paise, { signed: true })}
        </span>
      ),
    },
    { id: "started", header: "Started", accessorFn: (r) => r.started_at, cell: ({ getValue }) => <TimeAgo value={getValue() as string} className="text-xs" /> },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      enableSorting: false,
      cell: ({ row: { original: r } }) =>
        r.status === "active" && canAdmin ? (
          <Button variant="outline" size="xs" onClick={() => setEnding(r)}>
            {r.role === "consumer" ? "Release" : "End"}
          </Button>
        ) : null,
    },
  ]
  if (rentals.isError && !rentals.data) return <ErrorState error={rentals.error} onRetry={() => rentals.refetch()} />
  return (
    <>
      <DataTable
        columns={columns}
        data={rentals.data ?? []}
        loading={rentals.isLoading}
        getRowId={(r) => r.id}
        empty={<EmptyState bordered={false} icon={Cpu} title="No rentals yet" description="GPUs you rent, and GPUs others rent from you, appear here with what they cost or earned." />}
      />
      <ConfirmDialog
        open={ending !== null}
        onOpenChange={(o) => !o && setEnding(null)}
        title={ending?.role === "provider" ? "End this rental?" : "Release these GPUs?"}
        description={
          ending?.role === "provider"
            ? "The renter's running jobs on this node will fail. You are paid for the time used so far."
            : "Billing stops now. Jobs still running on these GPUs are cancelled."
        }
        confirmLabel={ending?.role === "provider" ? "End rental" : "Release"}
        destructive
        pending={release.isPending}
        onConfirm={() => {
          if (ending) release.mutate(ending)
        }}
      />
    </>
  )
}

function ListingsTable({ canAdmin, onList }: { canAdmin: boolean; onList: () => void }) {
  const ws = useWorkspace()
  const refresh = useRefresh()
  const offers = useOffers(true)
  const [removing, setRemoving] = useState<Offer | null>(null)
  const act = useMutation({
    mutationFn: async (a: { offer: Offer; op: "pause" | "resume" | "remove" }) => {
      if (a.op === "remove") await cloud.retireOffer(ws.id, a.offer.id)
      else await cloud.updateOffer(ws.id, a.offer.id, { status: a.op === "pause" ? "paused" : "listed" })
      return a.op
    },
    onSuccess: async (op) => {
      await refresh()
      toast.success(op === "remove" ? "Listing removed" : op === "pause" ? "Listing paused" : "Listing is live again")
      setRemoving(null)
    },
    onError: (e) => toast.error(message(e)),
  })
  const columns: ColumnDef<Offer, unknown>[] = [
    {
      accessorKey: "title",
      header: "Listing",
      cell: ({ row: { original: o } }) => (
        <div className="flex flex-col">
          <span className="font-medium">{o.title}</span>
          <span className="text-xs text-muted-foreground">
            {gpus(o)} · {formatVram(o.vram_bytes_per_gpu)} · {o.region}
            {o.visibility === "private" && ` · Private, shared with ${(o.shared_with ?? []).join(", ") || "no one yet"}`}
          </span>
        </div>
      ),
    },
    {
      id: "state",
      header: "Status",
      accessorFn: (o) => o.status,
      cell: ({ row: { original: o } }) =>
        o.status === "paused" ? (
          <StatusBadge tone="muted" size="sm">
            Paused
          </StatusBadge>
        ) : o.unavailable_reason === "rented" ? (
          <StatusBadge tone="info" size="sm">
            Rented
          </StatusBadge>
        ) : o.available ? (
          <StatusBadge tone="good" size="sm">
            Listed
          </StatusBadge>
        ) : (
          <StatusBadge tone="serious" size="sm" title={o.unavailable_reason}>
            Not rentable: {o.unavailable_reason}
          </StatusBadge>
        ),
    },
    { id: "price", header: "Per GPU-hour", meta: { align: "right" }, accessorFn: (o) => o.price_paise_per_gpu_hour, cell: ({ getValue }) => priceLabel(getValue() as number) },
    { id: "earn", header: "You earn per hour", meta: { align: "right" }, accessorFn: (o) => o.hourly_paise, cell: ({ getValue }) => priceLabel(getValue() as number) },
    {
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      enableSorting: false,
      cell: ({ row: { original: o } }) =>
        canAdmin ? (
          <div className="flex justify-end gap-1.5">
            <Button variant="outline" size="xs" disabled={act.isPending} onClick={() => act.mutate({ offer: o, op: o.status === "paused" ? "resume" : "pause" })}>
              {o.status === "paused" ? "Resume" : "Pause"}
            </Button>
            <Button variant="ghost" size="xs" onClick={() => setRemoving(o)}>
              Remove
            </Button>
          </div>
        ) : null,
    },
  ]
  if (offers.isError && !offers.data) return <ErrorState error={offers.error} onRetry={() => offers.refetch()} />
  return (
    <>
      <DataTable
        columns={columns}
        data={offers.data ?? []}
        loading={offers.isLoading}
        getRowId={(o) => o.id}
        empty={
          <EmptyState bordered={false} icon={Store} title="Nothing listed" description="List a node's GPUs to earn from capacity you are not using. You keep the full price you set.">
            {canAdmin && (
              <Button size="sm" onClick={onList}>
                List a node
              </Button>
            )}
          </EmptyState>
        }
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
        title="Remove this listing?"
        description="It disappears from the marketplace. The node stays in your fleet."
        confirmLabel="Remove listing"
        destructive
        pending={act.isPending}
        onConfirm={() => {
          if (removing) act.mutate({ offer: removing, op: "remove" })
        }}
      />
    </>
  )
}

export default function MarketplacePage() {
  const ws = useWorkspace()
  const offers = useOffers()
  const [params, setParams] = useSearchParams()
  const [renting, setRenting] = useState<Offer | null>(null)
  const tab = params.get("tab") ?? "rent"
  const listOpen = params.get("list") === "1"
  const setList = (o: boolean) => setParams(o ? { tab: "listings", list: "1" } : { tab: "listings" }, { replace: true })
  const canAdmin = atLeast(ws.role, "admin")
  const all = offers.data ?? []
  const open = all.filter((o) => o.available && !o.mine).length

  return (
    <Page>
      <PageHeader
        title="Marketplace"
        description="Rent GPUs from data centres across India, or list your own. Renters pay the provider's price plus a 2% Meridian fee, billed per second."
        actions={
          canAdmin && (
            <Button size="sm" variant="outline" onClick={() => setList(true)}>
              <Plus data-icon="inline-start" /> List a node
            </Button>
          )
        }
      />
      <Tabs value={tab} onValueChange={(t) => setParams({ tab: t }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="rent">Rent GPUs</TabsTrigger>
          <TabsTrigger value="rentals">Rentals</TabsTrigger>
          <TabsTrigger value="listings">Your listings</TabsTrigger>
        </TabsList>
        <TabsContent value="rent" className="space-y-3">
          {offers.isError && !offers.data ? (
            <ErrorState error={offers.error} onRetry={() => offers.refetch()} />
          ) : offers.isLoading ? (
            <LoadingGrid count={3} />
          ) : all.length === 0 ? (
            <EmptyState icon={Store} title="No GPUs are listed yet" description="When a provider lists capacity it appears here with its price. You can list your own nodes too." />
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                {open > 0 ? `${pluralize(open, "listing")} available to rent now, cheapest first.` : "Every listing is rented or offline right now."}
                {!canAdmin && " Renting needs the admin role."}
              </p>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {all.map((o) => (
                  <OfferCard key={o.id} offer={o} canRent={canAdmin} onRent={() => setRenting(o)} />
                ))}
              </div>
            </>
          )}
        </TabsContent>
        <TabsContent value="rentals">
          <RentalsTable canAdmin={canAdmin} />
        </TabsContent>
        <TabsContent value="listings">
          <ListingsTable canAdmin={canAdmin} onList={() => setList(true)} />
        </TabsContent>
      </Tabs>
      <RentDialog offer={renting} onClose={() => setRenting(null)} />
      {canAdmin && <ListNodeDialog open={listOpen} onOpenChange={setList} />}
    </Page>
  )
}
