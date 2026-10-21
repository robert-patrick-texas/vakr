/**
 * How the web UI refreshes and retries server data (ADR-035 decision 6; Appendix E.31).
 *
 * TanStack Query owns every API read. Pages refresh with `refetchInterval`,
 * which pauses while the tab is hidden (`refetchIntervalInBackground` is
 * false) and refreshes once when it is shown again. A refresh keeps the
 * previous answer on screen until the new one arrives, so nothing flashes
 * and keyboard focus stays where it was (UI-013, 12.8).
 *
 * Only idempotent reads retry, at most twice, and only when the backend did
 * not answer or answered 502/503/504; every 4xx is final. Writes never
 * retry. The numbers are the E.31 defaults.
 *
 * @example
 * ```ts
 * import { REFRESH } from '#lib/query/policy.ts';
 * createQuery(() => ({ queryKey, queryFn, refetchInterval: REFRESH.list }));
 * ```
 *
 * @packageDocumentation
 */
import { QueryClient } from '@tanstack/svelte-query';
import { isRetryable } from '#lib/api/errors.ts';

const SECOND = 1000;

/** Refresh interval per kind of page, milliseconds (Appendix E.31). */
export const REFRESH = {
	/** Overview, device and interface lists, high-priority, WAN. */
	list: 30 * SECOND,
	/** Device and interface detail (current values). */
	detail: 30 * SECOND,
	/** Collector health; a report is stale after 150 s at the 60 s default interval (E.3). */
	health: 30 * SECOND,
	/** Graphs: a new native point arrives at most once per 60 s poll. */
	graph: 60 * SECOND,
	/** Rankings: the window end is aligned to 1 or 5 minutes (E.28). */
	ranking: 60 * SECOND,
	/** Identity reviews. */
	reviews: 60 * SECOND
} as const;

/** Most retries of one read after the first attempt (ADR-035 decision 6). */
export const MAX_RETRIES = 2;
/** First retry delay; it doubles, up to `MAX_RETRY_DELAY`. */
export const RETRY_DELAY = SECOND;
/** Longest retry delay. */
export const MAX_RETRY_DELAY = 4 * SECOND;
/** An answer younger than this is not refetched when a page is revisited. */
export const STALE_TIME = 10 * SECOND;
/** An answer no page has used for this long is dropped from memory (bounds the cache). */
export const CACHE_TIME = 5 * 60 * SECOND;

/**
 * Decide whether a failed read is retried.
 *
 * @param retries - Retries already made (TanStack's `failureCount`: 0 after the first failure).
 * @param error - The last error.
 * @returns True while under `MAX_RETRIES` and the error is a backend outage.
 */
export function shouldRetry(retries: number, error: unknown): boolean {
	return retries < MAX_RETRIES && isRetryable(error);
}

/**
 * Delay before the next retry.
 *
 * @param attempt - Zero-based retry number.
 * @returns Milliseconds: 1 s, then 2 s, at most 4 s.
 */
export function retryDelay(attempt: number): number {
	return Math.min(RETRY_DELAY * 2 ** attempt, MAX_RETRY_DELAY);
}

/**
 * Build the application's query client with vakr's defaults.
 *
 * @returns The client.
 */
export function createQueryClient(): QueryClient {
	return new QueryClient({
		defaultOptions: {
			queries: {
				retry: shouldRetry,
				retryDelay,
				staleTime: STALE_TIME,
				gcTime: CACHE_TIME,
				refetchIntervalInBackground: false,
				refetchOnWindowFocus: true,
				structuralSharing: true
			},
			mutations: { retry: false }
		}
	});
}
