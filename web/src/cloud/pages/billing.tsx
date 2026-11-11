import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Download, IndianRupee, Users, Wallet } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { DataTable, type ColumnDef } from "@/components/data-table"
import { Timestamp } from "@/components/misc"
import { Page, PageHeader, Panel } from "@/components/page"
import { EmptyState, ErrorState } from "@/components/states"
import { StatCard } from "@/components/stat"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { ApiError } from "@/lib/api/client"
import { formatINR } from "@/lib/format"

import { cloud, rupeesToPaise, type LedgerEntry, type Usage, type UsageRow } from "../api"
import { useBilling, usePlatformSummary, useUsage } from "../hooks"
import { useCloud, useWorkspace } from "../session"

const KIND: Record<string, string> = {
  topup: "Credit added",
  rental_charge: "GPU rental",
  platform_fee: "Meridian fee",
  rental_earning: "Rental earnings",
  payout: "Payout to bank",
  adjustment: "Adjustment",
}

const columns: ColumnDef<LedgerEntry, unknown>[] = [
  { accessorKey: "created_at", header: "When", cell: ({ getValue }) => <Timestamp value={getValue() as string} /> },
  { accessorKey: "kind", header: "What", cell: ({ getValue }) => KIND[getValue() as string] ?? (getValue() as string) },
  {
    id: "detail",
    header: "Detail",
    enableSorting: false,
    cell: ({ row: { original: e } }) => (
      <span className="text-xs text-muted-foreground">{[e.reference, e.rental_id, e.memo].filter(Boolean).join(" · ") || "—"}</span>
    ),
  },
  {
    accessorKey: "amount_paise",
    header: "Amount",
    meta: { align: "right" },
    cell: ({ getValue }) => <span className="tabular-nums">{formatINR(getValue() as number, { signed: true })}</span>,
  },
]

const hours = (n: number) => n.toFixed(2)

const usageColumns: ColumnDef<UsageRow, unknown>[] = [
  {
    accessorKey: "email",
    header: "Person",
    cell: ({ row: { original: p } }) => (
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">{p.name || p.email}</div>
        {p.name && <div className="truncate text-xs text-muted-foreground">{p.email}</div>}
      </div>
    ),
  },
  { accessorKey: "group", header: "Group", cell: ({ getValue }) => (getValue() as string) || "—" },
  { accessorKey: "gpu_hours_own", header: "Own GPU-hours", meta: { align: "right" }, cell: ({ getValue }) => <span className="tabular-nums">{hours(getValue() as number)}</span> },
  { accessorKey: "gpu_hours_rented", header: "Rented GPU-hours", meta: { align: "right" }, cell: ({ getValue }) => <span className="tabular-nums">{hours(getValue() as number)}</span> },
  { accessorKey: "jobs", header: "Jobs", meta: { align: "right" } },
  {
    accessorKey: "quota_gpu_hours",
    header: "Quota",
    meta: { align: "right" },
    cell: ({ getValue }) => <span className="tabular-nums">{(getValue() as number | null) ?? "Unlimited"}</span>,
  },
  {
    accessorKey: "remaining_gpu_hours",
    header: "Left",
    meta: { align: "right" },
    cell: ({ getValue }) => <span className="tabular-nums">{getValue() === null ? "—" : hours(getValue() as number)}</span>,
  },
]

const groupColumns: ColumnDef<Usage["groups"][number], unknown>[] = [
  { accessorKey: "group", header: "Group", cell: ({ getValue }) => (getValue() as string) || "No group" },
  { accessorKey: "members", header: "People", meta: { align: "right" } },
  { accessorKey: "jobs", header: "Jobs", meta: { align: "right" } },
  { accessorKey: "gpu_hours_own", header: "Own GPU-hours", meta: { align: "right" }, cell: ({ getValue }) => <span className="tabular-nums">{hours(getValue() as number)}</span> },
  { accessorKey: "gpu_hours_rented", header: "Rented GPU-hours", meta: { align: "right" }, cell: ({ getValue }) => <span className="tabular-nums">{hours(getValue() as number)}</span> },
]

/** The last twelve UTC months, newest first, as YYYY-MM. */
function recentMonths(now = new Date()): string[] {
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1))
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`
  })
}

/** Who used how many GPU-hours. Admins see everyone; other members see their own row and the workspace total. */
function UsagePanel() {
  const ws = useWorkspace()
  const months = recentMonths()
  const [month, setMonth] = useState(months[0])
  const usage = useUsage(month)
  const u = usage.data
  return (
    <Panel
      title="GPU-hour usage"
      description="Per person, in UTC months. Own-node jobs are free but count against quotas; rented GPUs are paid for with credit. Quotas and groups are set in Settings → Members."
      actions={
        <div className="flex items-center gap-2">
          <Select value={month} onValueChange={setMonth}>
            <SelectTrigger size="sm" className="w-32" aria-label="Month">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {months.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" asChild>
            <a href={cloud.usageCsvUrl(ws.id, month)} download>
              <Download data-icon="inline-start" /> CSV
            </a>
          </Button>
        </div>
      }
      contentClassName="px-0"
    >
      {usage.isError && !u ? (
        <ErrorState error={usage.error} onRetry={() => usage.refetch()} />
      ) : (
        <div className="flex flex-col gap-4">
          <DataTable
            columns={usageColumns}
            data={u?.people}
            loading={usage.isLoading}
            getRowId={(p) => p.email}
            pageSize={25}
            empty={<EmptyState bordered={false} icon={Users} title="No usage this month" description="GPU-hours appear here once jobs have run." />}
          />
          {!!u?.people.length && (
            <>
              {u.scope === "workspace" ? (
                <DataTable columns={groupColumns} data={u.groups} getRowId={(g) => g.group || "(none)"} pageSize={25} />
              ) : (
                <p className="px-4 text-sm text-muted-foreground">You see your own usage. Admins see everyone's.</p>
              )}
              <p className="px-4 text-sm text-muted-foreground">
                Workspace total in {u.month}: {hours(u.totals.gpu_hours_own)} own and {hours(u.totals.gpu_hours_rented)} rented GPU-hours across {u.totals.jobs}{" "}
                {u.totals.jobs === 1 ? "job" : "jobs"}.
              </p>
            </>
          )}
        </div>
      )}
    </Panel>
  )
}

/** Shown only to the people who operate this Meridian Cloud. */
function PlatformPanel() {
  const ws = useWorkspace()
  const qc = useQueryClient()
  const summary = usePlatformSummary(true)
  const [target, setTarget] = useState(ws.id)
  const [kind, setKind] = useState<"credit" | "payout">("credit")
  const [amount, setAmount] = useState("")
  const [reference, setReference] = useState("")
  const paise = rupeesToPaise(amount)
  const record = useMutation({
    mutationFn: () => {
      const body = { workspace: target, amount_paise: paise ?? 0, reference: reference.trim(), memo: "" }
      return kind === "credit" ? cloud.platformCredit(body) : cloud.platformPayout(body)
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: ["cloud"] })
      toast.success(`${kind === "credit" ? "Credit" : "Payout"} recorded. New balance ${formatINR(r.balance_paise)}`)
      setAmount("")
      setReference("")
    },
  })
  const ready = paise !== null && paise > 0 && reference.trim().length >= 4
  const s = summary.data
  return (
    <Panel
      title="Platform administration"
      description="You operate this Meridian Cloud. Record money received from, or paid out to, a workspace. Each entry needs the bank or payment reference."
    >
      <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <dt className="text-muted-foreground">Fee revenue</dt>
          <dd className="text-right font-medium tabular-nums">{s ? formatINR(s.fee_revenue_paise) : "…"}</dd>
          <dt className="text-muted-foreground">Rental volume</dt>
          <dd className="text-right font-medium tabular-nums">{s ? formatINR(s.gross_rental_paise) : "…"}</dd>
          <dt className="text-muted-foreground">Active rentals</dt>
          <dd className="text-right font-medium tabular-nums">{s?.active_rentals ?? "…"}</dd>
          <dt className="text-muted-foreground">Live listings</dt>
          <dd className="text-right font-medium tabular-nums">{s?.listed_offers ?? "…"}</dd>
        </dl>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) record.mutate()
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="pf-ws">Workspace</FieldLabel>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger id="pf-ws" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(s?.workspaces ?? [{ id: ws.id, name: ws.name, slug: ws.slug, balance_paise: 0 }]).map((w) => (
                    <SelectItem key={w.id} value={w.id}>
                      {w.name} · {formatINR(w.balance_paise)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="pf-kind">Entry</FieldLabel>
              <Select value={kind} onValueChange={(v) => setKind(v as "credit" | "payout")}>
                <SelectTrigger id="pf-kind" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="credit">Credit: money received</SelectItem>
                  <SelectItem value="payout">Payout: earnings paid out</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="pf-amount">Amount (₹)</FieldLabel>
              <Input id="pf-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="e.g. 25000" />
            </Field>
            <Field>
              <FieldLabel htmlFor="pf-ref">Payment reference</FieldLabel>
              <Input id="pf-ref" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={200} placeholder="UTR or payment ID" />
            </Field>
          </div>
          <FieldDescription>A reference can be credited only once, so the same payment is never counted twice.</FieldDescription>
          {record.isError && (
            <p role="alert" className="text-sm text-destructive">
              {record.error instanceof ApiError ? record.error.message : "Could not record the entry."}
            </p>
          )}
          <Button type="submit" size="sm" disabled={!ready || record.isPending}>
            {record.isPending && <Spinner data-icon="inline-start" />}
            Record {kind}
          </Button>
        </form>
      </div>
    </Panel>
  )
}

export default function BillingPage() {
  const { me } = useCloud()
  const billing = useBilling()
  const b = billing.data
  if (billing.isError && !b) return <Page><ErrorState error={billing.error} onRetry={() => billing.refetch()} /></Page>
  const hoursLeft = b && b.burn_paise_per_hour > 0 ? Math.max(0, b.available_paise) / b.burn_paise_per_hour : null
  const spent = -((b?.totals_paise.rental_charge ?? 0) + (b?.totals_paise.platform_fee ?? 0))
  const earned = b?.totals_paise.rental_earning ?? 0

  return (
    <Page>
      <PageHeader
        title="Billing"
        description="Prepaid credit in rupees. Rentals draw it down per second; GPUs you rent out add to it."
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Available credit"
          icon={Wallet}
          loading={!b}
          value={formatINR(b?.available_paise)}
          tone={b && b.available_paise <= 0 && b.burn_paise_per_hour > 0 ? "critical" : undefined}
          hint={b && b.accrued_spend_paise > 0 ? `After ${formatINR(b.accrued_spend_paise)} accrued on running rentals` : "Balance, ready to spend"}
        />
        <StatCard
          label="Spending now"
          icon={IndianRupee}
          loading={!b}
          value={b && b.burn_paise_per_hour > 0 ? formatINR(b.burn_paise_per_hour) : "₹0.00"}
          unit="/hour"
          tone={hoursLeft !== null && hoursLeft < 2 ? "warning" : undefined}
          hint={hoursLeft === null ? "No rentals running" : `About ${hoursLeft < 10 ? hoursLeft.toFixed(1) : Math.round(hoursLeft)} hours of credit left`}
        />
        <StatCard label="Spent on rentals" loading={!b} value={formatINR(spent)} hint={`Includes ${formatINR(-(b?.totals_paise.platform_fee ?? 0))} in fees`} />
        <StatCard
          label="Earned from rentals"
          loading={!b}
          value={formatINR(earned)}
          hint={b && b.accrued_earnings_paise > 0 ? `Plus ${formatINR(b.accrued_earnings_paise)} accruing now` : "You keep the full price you set"}
        />
      </div>
      <Panel
        title="Add credit"
        description={`Renters pay the provider's price plus a ${(b?.fee_bps ?? 200) / 100}% Meridian fee. Providers are paid the full price they set.`}
      >
        <p className="max-w-3xl text-sm text-muted-foreground">
          Credit is added by bank transfer or payment link: contact your Meridian account manager with the amount, and the credit appears here against your
          payment reference. Earnings are paid out to your bank account on request. Card and UPI checkout are not available yet.
        </p>
      </Panel>
      {me?.platform_admin && <PlatformPanel />}
      <UsagePanel />
      <Panel title="Ledger" description="Every credit and debit, newest first. Running rentals post about every ten minutes and when they end." contentClassName="px-0">
        <DataTable
          columns={columns}
          data={b?.entries ?? []}
          loading={billing.isLoading}
          getRowId={(e) => String(e.id)}
          pageSize={25}
          empty={<EmptyState bordered={false} icon={Wallet} title="No transactions yet" description="Credits, rental charges and earnings will be listed here." />}
        />
      </Panel>
    </Page>
  )
}
