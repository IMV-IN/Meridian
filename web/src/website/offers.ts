// The public marketplace feed: GET /api/public/offers (meridian_control/compute.py, public_offers).
// Same origin as the site, so no CORS. Money is integer paise.
//
// The feed does not name providers (they have not agreed to it), so the site shows the region instead.

export interface PublicOffer {
  title: string
  region: string
  gpu_model: string
  gpu_count: number
  vram_bytes_per_gpu: number
  price_paise_per_gpu_hour: number
  hourly_total_paise: number
  available: boolean
}

export interface PublicOffers {
  offers: PublicOffer[]
  total_offers: number
  summary: {
    available_gpus: number
    gpus_by_model: Record<string, number>
    regions: string[]
    lowest_price_paise_per_gpu_hour: number | null
  }
}

/** The feed did not answer. `status` is 0 when the request never got a response. */
export class FeedError extends Error {
  status: number
  constructor(status: number) {
    super(status ? `HTTP ${status}` : "no response")
    this.status = status
  }
}

export const FEED_URL = "/api/public/offers"
export const FEED_REFRESH_MS = 30_000

export async function fetchPublicOffers(signal?: AbortSignal): Promise<PublicOffers> {
  let res: Response
  try {
    res = await fetch(FEED_URL, { headers: { Accept: "application/json" }, signal })
  } catch (e) {
    if (signal?.aborted) throw e
    throw new FeedError(0)
  }
  if (!res.ok) throw new FeedError(res.status)
  let body: Partial<PublicOffers>
  try {
    body = (await res.json()) as Partial<PublicOffers>
  } catch {
    throw new FeedError(res.status)
  }
  if (!Array.isArray(body.offers)) throw new FeedError(res.status)
  return { ...body, offers: body.offers, total_offers: body.total_offers ?? body.offers.length } as PublicOffers
}

export type SortKey = "price" | "gpus" | "vram"
export const SORTS: { key: SortKey; label: string }[] = [
  { key: "price", label: "Cheapest first" },
  { key: "gpus", label: "Most GPUs first" },
  { key: "vram", label: "Most memory first" },
]
export const isSort = (v: string): v is SortKey => SORTS.some((s) => s.key === v)

/** Available listings always come first; then the chosen order. Does not change the input. */
export function arrange(offers: PublicOffer[], gpu: string, sort: SortKey): PublicOffer[] {
  const by = {
    price: (a: PublicOffer, b: PublicOffer) => a.price_paise_per_gpu_hour - b.price_paise_per_gpu_hour,
    gpus: (a: PublicOffer, b: PublicOffer) => b.gpu_count - a.gpu_count,
    vram: (a: PublicOffer, b: PublicOffer) => b.vram_bytes_per_gpu * b.gpu_count - a.vram_bytes_per_gpu * a.gpu_count,
  }[sort]
  return offers
    .filter((o) => !gpu || o.gpu_model === gpu)
    .sort((a, b) => Number(b.available) - Number(a.available) || by(a, b))
}

export const gpuModels = (offers: PublicOffer[]) => [...new Set(offers.map((o) => o.gpu_model))].sort()

/** The TanStack Query options for the feed: refreshed every 30 seconds and when the tab regains focus. */
export function feedQuery() {
  return {
    queryKey: ["public-offers"],
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchPublicOffers(signal),
    refetchInterval: FEED_REFRESH_MS,
    refetchOnWindowFocus: true,
    staleTime: 15_000,
  }
}
