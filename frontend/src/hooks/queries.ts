import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"

import { api, query } from "@/lib/api"
import type {
  ChainResponse,
  CollectionStatus,
  ContractSeriesResponse,
  ChainSummaryRow,
  Group,
  MetricDescriptor,
  SessionProfileResponse,
  TermStructureResponse,
  Underlying,
  UnderlyingStatus,
} from "@/types"

/** One place that knows every key, so an invalidation can never miss a cache. */
export const keys = {
  underlyings: ["underlyings"] as const,
  underlyingStatus: (symbol: string) => ["underlyings", symbol, "status"] as const,
  groups: ["groups"] as const,
  metrics: ["chains", "metrics"] as const,
  chainSummary: (groupId?: number) => ["chains", "summary", groupId ?? "all"] as const,
  chain: (symbol: string, at?: string) => ["chains", symbol, at ?? "latest"] as const,
  term: (symbol: string, at?: string) => ["chains", symbol, "term", at ?? "latest"] as const,
  contract: (occ: string, days: number) => ["contracts", occ, days] as const,
  session: (symbol: string, expiration: string, on?: string) =>
    ["chains", symbol, "session", expiration, on ?? "latest"] as const,
  collection: ["system", "collection"] as const,
}

/**
 * The collector polls on a 5-minute base cadence, so a 60s refetch is the
 * shortest interval that can ever show something new without wasting requests.
 */
const POLL_MS = 60_000

export function useUnderlyings() {
  return useQuery({
    queryKey: keys.underlyings,
    queryFn: () => api.get<Underlying[]>("/underlyings"),
  })
}

export function useUnderlyingStatus(symbol: string | undefined) {
  return useQuery({
    queryKey: keys.underlyingStatus(symbol ?? ""),
    queryFn: () => api.get<UnderlyingStatus>(`/underlyings/${symbol}/status`),
    enabled: Boolean(symbol),
    refetchInterval: POLL_MS,
  })
}

/**
 * Collection health for many underlyings at once.
 *
 * One request per symbol, which is what the API offers: status is a count
 * query per underlying and there is no batch endpoint for it. Fetching them
 * here rather than inside each row is what lets the caller sort by staleness,
 * which needs every result before it can order anything.
 */
export function useUnderlyingStatuses(symbols: string[]) {
  return useQueries({
    queries: symbols.map((symbol) => ({
      queryKey: keys.underlyingStatus(symbol),
      queryFn: () => api.get<UnderlyingStatus>(`/underlyings/${symbol}/status`),
      refetchInterval: POLL_MS,
    })),
    combine: (results) => ({
      bySymbol: new Map(
        results.map((result, index) => [symbols[index], result.data] as const),
      ),
      isPending: results.some((result) => result.isPending),
    }),
  })
}

export function useGroups() {
  return useQuery({ queryKey: keys.groups, queryFn: () => api.get<Group[]>("/groups") })
}

/**
 * Spot and chain metrics for every tracked underlying in one request.
 *
 * The overview and each group page render the same table off this. Pulling a
 * full chain per underlying to compute the same numbers client side is what
 * this replaces.
 */
export function useChainSummary(groupId?: number) {
  return useQuery({
    queryKey: keys.chainSummary(groupId),
    queryFn: () =>
      api.get<ChainSummaryRow[]>(`/chains/summary${query({ group_id: groupId })}`),
    refetchInterval: POLL_MS,
  })
}

export function useMetricDescriptors() {
  return useQuery({
    queryKey: keys.metrics,
    queryFn: () => api.get<MetricDescriptor[]>("/chains/metrics"),
    // The registry only changes when the backend is redeployed.
    staleTime: Infinity,
  })
}

type ChainOptions = Pick<UseQueryOptions<ChainResponse>, "enabled">

export function useChain(symbol: string | undefined, at?: string, options?: ChainOptions) {
  return useQuery({
    queryKey: keys.chain(symbol ?? "", at),
    queryFn: () => api.get<ChainResponse>(`/chains/${symbol}${query({ at })}`),
    enabled: Boolean(symbol) && options?.enabled !== false,
    refetchInterval: POLL_MS,
    // A whole chain is thousands of rows; keep the last one on screen while the
    // next arrives rather than flashing a skeleton.
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useTermStructure(symbol: string | undefined, at?: string) {
  return useQuery({
    queryKey: keys.term(symbol ?? "", at),
    queryFn: () => api.get<TermStructureResponse>(`/chains/${symbol}/term${query({ at })}`),
    enabled: Boolean(symbol),
    refetchInterval: POLL_MS,
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useSessionProfile(
  symbol: string | undefined,
  expiration: string | null,
  session: string | null,
) {
  return useQuery({
    queryKey: keys.session(symbol ?? "", expiration ?? "", session ?? undefined),
    queryFn: () =>
      api.get<SessionProfileResponse>(
        `/chains/${symbol}/session${query({ expiration, session })}`,
      ),
    // Both filters are optional: with neither, the backend picks its most
    // recent session and that session's nearest expiry, and hands back both
    // lists so the pickers can populate from one round trip.
    enabled: Boolean(symbol),
    // A finished session never changes, so there is nothing to poll for.
    staleTime: 5 * 60_000,
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useContractSeries(occSymbol: string | undefined, days: number) {
  return useQuery({
    queryKey: keys.contract(occSymbol ?? "", days),
    queryFn: () => api.get<ContractSeriesResponse>(`/chains/contracts/${occSymbol}${query({ days })}`),
    enabled: Boolean(occSymbol),
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useCollectionStatus() {
  return useQuery({
    queryKey: keys.collection,
    queryFn: () => api.get<CollectionStatus>("/system/collection"),
    refetchInterval: POLL_MS,
  })
}

export function useAddUnderlying() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { symbol: string; group_id?: number | null }) =>
      api.post<Underlying>("/underlyings", input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.underlyings })
      void client.invalidateQueries({ queryKey: keys.groups })
      void client.invalidateQueries({ queryKey: keys.collection })
    },
  })
}

export function useUpdateUnderlying() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ symbol, ...patch }: { symbol: string; is_active?: boolean; dividend_yield?: number }) =>
      api.patch<Underlying>(`/underlyings/${symbol}`, patch),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.underlyings })
      void client.invalidateQueries({ queryKey: keys.collection })
    },
  })
}

export function useDeleteUnderlying() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (symbol: string) => api.delete<void>(`/underlyings/${symbol}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.underlyings })
      void client.invalidateQueries({ queryKey: keys.groups })
    },
  })
}

export function useCreateGroup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { name: string; description?: string | null }) => api.post<Group>("/groups", input),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.groups }),
  })
}

export function useDeleteGroup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/groups/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.groups }),
  })
}

export function useAddToGroup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ groupId, underlyingIds }: { groupId: number; underlyingIds: number[] }) =>
      api.post<Group>(`/groups/${groupId}/underlyings`, { underlying_ids: underlyingIds }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.groups })
      void client.invalidateQueries({ queryKey: keys.collection })
    },
  })
}

export function useRemoveFromGroup() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ groupId, underlyingId }: { groupId: number; underlyingId: number }) =>
      api.delete<Group>(`/groups/${groupId}/underlyings/${underlyingId}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.groups })
      void client.invalidateQueries({ queryKey: keys.collection })
    },
  })
}
