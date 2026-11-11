// GPUs listed right now: the public feed, GET /api/public/offers, refreshed every 30 seconds.
// Providers are not named (they have not agreed to it): each listing shows its region.
import { useQuery } from "@tanstack/react-query"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useUrlState } from "@/hooks/use-url-state"
import { formatINR, formatVram } from "@/lib/format"

import { arrange, feedQuery, FeedError, gpuModels, isSort, SORTS, type PublicOffer } from "./offers"

const ALL = "all"

const label = (o: PublicOffer) => `${o.gpu_count} × ${o.gpu_model}`

function Skeletons() {
  return (
    <table className="otable" aria-hidden="true">
      <thead>
        <tr>
          <th>GPUs</th>
          <th>Region</th>
          <th className="r">Per GPU-hour</th>
          <th className="r">Whole listing, per hour</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {[0, 1, 2].map((i) => (
          <tr key={i}>
            <td className="c-gpu">
              <Skeleton className="sk" style={{ width: "70%" }} />
            </td>
            <td className="c-prov">
              <Skeleton className="sk" style={{ width: "60%" }} />
            </td>
            <td className="c-price">
              <Skeleton className="sk" style={{ width: "60%" }} />
            </td>
            <td className="c-total">
              <Skeleton className="sk" style={{ width: "60%" }} />
            </td>
            <td className="c-act">
              <Skeleton className="sk" style={{ width: "80%" }} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function LiveListings({ onPrice }: { onPrice: (o: PublicOffer) => void }) {
  const q = useQuery(feedQuery())
  const [gpu, setGpu] = useUrlState("gpu")
  const [sortParam, setSort] = useUrlState("sort", "price")
  const sort = isSort(sortParam) ? sortParam : "price"
  const data = q.data
  const loading = q.isPending || (!data && q.isFetching) // a retry after an error shows the loading state again
  const failed = q.isError && !loading
  const offers = data?.offers ?? []
  const rows = arrange(offers, gpu, sort)
  const available = offers.filter((o) => o.available).length
  const total = data?.total_offers ?? 0

  let dot = "off"
  let meta = "Loading listings"
  if (failed && data) {
    dot = "err"
    meta = `Showing listings from ${new Date(q.dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}. The feed did not answer; retrying.`
  } else if (failed) {
    dot = "err"
    meta = "Listings unavailable"
  } else if (data) {
    dot = offers.length ? "" : "off"
    meta = offers.length ? `${available} of ${total} ${total === 1 ? "listing" : "listings"} available. Updated every 30 seconds.` : "No listings right now."
  }

  return (
    <div className="market" id="listings">
      <div className="market-head">
        <div>
          <h3>GPUs listed right now</h3>
          <p className="feed-meta" aria-live="polite">
            <span className={`live-dot ${dot}`} aria-hidden="true" />
            {meta}
          </p>
        </div>
        {offers.length > 0 && (
          <div className="market-tools">
            <div className="field-inline">
              <span id="f-gpu">GPU</span>
              <Select value={gpu || ALL} onValueChange={(v) => setGpu(v === ALL ? "" : v)}>
                <SelectTrigger className="site-select" aria-labelledby="f-gpu">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All GPUs</SelectItem>
                  {gpuModels(offers).map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="field-inline">
              <span id="f-sort">Sort</span>
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger className="site-select" aria-labelledby="f-sort">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORTS.map((s) => (
                    <SelectItem key={s.key} value={s.key}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}
      </div>

      <div className="offers" aria-busy={loading}>
        {loading ? (
          <>
            <Skeletons />
            <span className="sr-only">Loading listings</span>
          </>
        ) : !data ? (
          <div className="state" role="alert">
            <h4>Listings could not be loaded</h4>
            <p>
              The marketplace feed did not answer{q.error instanceof FeedError && q.error.status ? ` (HTTP ${q.error.status})` : ""}. Rentals you already hold are not affected. Try again, or see the listings in the console.
            </p>
            <div className="state-actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void q.refetch()}>
                Try again
              </button>
              <a className="btn btn-ghost btn-sm" href="/ui/marketplace">
                Open the marketplace in the console
              </a>
            </div>
          </div>
        ) : offers.length === 0 ? (
          <div className="state">
            <h4>No GPUs are listed right now</h4>
            <p>When a provider lists capacity it appears here with its price. If your institute has idle servers, you can be the first.</p>
            <div className="state-actions">
              <a className="btn btn-lamp btn-sm" href="/ui/marketplace?tab=listings">
                List a node
              </a>
              <a className="btn btn-ghost btn-sm" href="#calc">
                Estimate a rental
              </a>
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="state">
            <h4>No {gpu} listed right now</h4>
            <p>Try another GPU, or show every listing.</p>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setGpu("")}>
              Show all GPUs
            </button>
          </div>
        ) : (
          <table className="otable">
            <caption className="sr-only">GPU listings</caption>
            <thead>
              <tr>
                <th scope="col">GPUs</th>
                <th scope="col">Region</th>
                <th scope="col" className="r">
                  Per GPU-hour
                </th>
                <th scope="col" className="r">
                  Whole listing, per hour with 2% fee
                </th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o, i) => (
                <tr key={`${o.title}-${i}`} className={o.available ? undefined : "is-rented"}>
                  <td className="c-gpu">
                    <span className="gpu-name">{label(o)}</span>
                    <span className="sub">
                      {formatVram(o.vram_bytes_per_gpu)} each{o.available ? "" : ", rented now"}
                    </span>
                  </td>
                  <td className="c-prov">{o.region}</td>
                  <td className="c-price r">
                    <span className="price num">{formatINR(o.price_paise_per_gpu_hour)}</span>
                  </td>
                  <td className="c-total r num">{formatINR(o.hourly_total_paise)}</td>
                  <td className="c-act">
                    <div className="acts">
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onPrice(o)} aria-label={`Price ${label(o)} in the calculator`}>
                        Price it
                      </button>
                      {o.available ? (
                        <a className="btn btn-lamp btn-sm" href="/ui/marketplace" aria-label={`Rent ${label(o)} in the console`}>
                          Rent
                        </a>
                      ) : (
                        <span className="btn btn-sm status-no">Rented</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="caption" style={{ marginTop: 12 }}>
        Listings come from <code>GET /api/public/offers</code>. Renting happens in the console, where a rental needs one hour of credit up front.
      </p>
    </div>
  )
}
