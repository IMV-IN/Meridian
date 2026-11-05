import { QueryCache, QueryClient } from "@tanstack/react-query"

import { ApiError } from "@/lib/api/client"

export const CLOUD_ME_KEY = ["cloud", "me"] as const
// 401/403/404 are answers, not transient failures: don't retry them (a 401
// must flip to sign-in immediately, not after the default backoff).
export const retryTransient = (count: number, err: unknown) =>
  !(err instanceof ApiError && (err.isUnauthorized || err.isForbidden || err.isNotFound)) && count < 2

const isMe = (key: readonly unknown[]) => key[0] === CLOUD_ME_KEY[0] && key[1] === CLOUD_ME_KEY[1]

/**
 * The console's query client. In Meridian Cloud any 401 means the session
 * ended (expired, revoked, signed out in another tab): re-check /api/me so
 * the shell flips to sign-in, and drop cached workspace data so the next
 * account to sign in on this browser never sees it.
 */
export function makeQueryClient({ cloud = false }: { cloud?: boolean } = {}) {
  const qc: QueryClient = new QueryClient({
    queryCache: cloud
      ? new QueryCache({
          onError: (err, query) => {
            if (!(err instanceof ApiError) || !err.isUnauthorized) return
            if (isMe(query.queryKey)) qc.removeQueries({ predicate: (q) => !isMe(q.queryKey) })
            else void qc.invalidateQueries({ queryKey: CLOUD_ME_KEY })
          },
        })
      : undefined,
    defaultOptions: {
      queries: {
        staleTime: 1_000,
        retry: retryTransient,
        refetchOnWindowFocus: false,
        // Live views pause in background tabs; no point polling hidden pages.
        refetchIntervalInBackground: false,
      },
    },
  })
  return qc
}
