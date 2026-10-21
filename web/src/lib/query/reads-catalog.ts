/**
 * Query options for the catalog, collectors, identity reviews, search, and health
 * (12.1, 12.3, UI-001, UI-009, IFID-007).
 *
 * Sites, tags, and groups change rarely, so they are cached for 5 minutes
 * without polling; collector health and reviews refresh on the E.31
 * intervals; search runs only when asked.
 *
 * @example
 * ```ts
 * createQuery(() => searchQuery('core'));
 * ```
 *
 * @packageDocumentation
 */
import { keepPreviousData } from '@tanstack/svelte-query';
import { api, unwrap } from '#lib/api/client.ts';
import { REFRESH } from './policy.ts';

const CATALOG_TTL = 5 * 60 * 1000;
/** Rows per catalog page; the API allows up to 500 (E.19). */
export const CATALOG_PAGE = 500;

/**
 * One page of sites, with code, region, and time zone.
 *
 * @param cursor - Page cursor.
 * @returns Query options.
 */
export function sitesQuery(cursor: string | null = null) {
	return {
		queryKey: ['catalog', 'sites', cursor] as const,
		queryFn: () =>
			unwrap(api.GET('/api/v1/sites', { params: { query: { limit: CATALOG_PAGE, cursor } } })),
		staleTime: CATALOG_TTL,
		placeholderData: keepPreviousData
	};
}

/**
 * One page of tags or groups.
 *
 * @param kind - Which catalog.
 * @param cursor - Page cursor.
 * @returns Query options.
 */
export function labelsQuery(kind: 'tags' | 'groups', cursor: string | null = null) {
	const query = { params: { query: { limit: CATALOG_PAGE, cursor } } };
	return {
		queryKey: ['catalog', kind, cursor] as const,
		queryFn: () =>
			kind === 'tags'
				? unwrap(api.GET('/api/v1/tags', query))
				: unwrap(api.GET('/api/v1/groups', query)),
		staleTime: CATALOG_TTL,
		placeholderData: keepPreviousData
	};
}

/**
 * Every polling position with its latest health report (UI-001 collector health).
 *
 * @returns Query options.
 */
export function collectorsQuery() {
	return {
		queryKey: ['collectors'] as const,
		queryFn: () => unwrap(api.GET('/api/v1/collectors', { params: { query: { limit: 500 } } })),
		refetchInterval: REFRESH.health
	};
}

/**
 * The API's own health: components with reasons, the release, the start time.
 *
 * @returns Query options.
 */
export function healthQuery() {
	return {
		queryKey: ['health-detail'] as const,
		queryFn: () => unwrap(api.GET('/api/v1/health/detail')),
		refetchInterval: REFRESH.health
	};
}

/**
 * One page of open identity reviews, oldest first (IFID-007).
 *
 * @param device - Only this device's reviews, if set.
 * @param cursor - Page cursor.
 * @returns Query options.
 */
export function reviewsQuery(device: string | null, cursor: string | null) {
	return {
		queryKey: ['reviews', device, cursor] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/identity-reviews', { params: { query: { device, cursor, limit: 50 } } })
			),
		refetchInterval: REFRESH.reviews,
		placeholderData: keepPreviousData
	};
}

/**
 * Global search (12.1, UI-017).
 *
 * @param q - Plain text, at most 256 characters.
 * @returns Query options; disabled for empty text.
 */
export function searchQuery(q: string) {
	return {
		queryKey: ['search', q] as const,
		queryFn: () => unwrap(api.GET('/api/v1/search', { params: { query: { q, per_kind: 50 } } })),
		enabled: q.trim().length > 0,
		placeholderData: keepPreviousData
	};
}
