"use client";

import useSWR, { type SWRConfiguration } from "swr";

/**
 * Generic SWR-backed hook for engine REST API calls.
 *
 * Usage:
 *   const { data, error, isLoading, mutate } = useApi("/status", getStatus);
 */
export function useApi<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  config?: SWRConfiguration<T>
) {
  return useSWR<T>(key, fetcher, {
    refreshInterval: 5_000,    // poll every 5 s
    revalidateOnFocus: true,
    dedupingInterval: 2_000,
    ...config,
  });
}

/**
 * One-shot API call hook (no polling).
 */
export function useApiOnce<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  config?: SWRConfiguration<T>
) {
  return useSWR<T>(key, fetcher, {
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
    ...config,
  });
}
