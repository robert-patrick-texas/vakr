/**
 * List filters kept in the page URL, so a filtered view can be bookmarked and shared
 * (12.3 "saved URL query state", UI-009).
 *
 * `readDeviceFilters` and `readInterfaceFilters` accept only values the API
 * accepts (known buckets, sorts, and admin states; UUID-shaped catalog
 * filters; page sizes within E.19) and drop anything else, so a hand-edited
 * URL cannot produce a 422 loop. `toQuery` writes back only what differs
 * from the defaults.
 *
 * @example
 * ```ts
 * readDeviceFilters(new URLSearchParams('bucket=attention&sort=name')).sort; // 'name'
 * ```
 *
 * @packageDocumentation
 */
import type { DeviceQuery, InterfaceQuery } from '#lib/query/reads.ts';

/** The read side of URL parameters (SvelteKit's `page.url.searchParams` is read-only). */
export type Params = Pick<URLSearchParams, 'get' | 'getAll'>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const PAGE_SIZES = [25, 50, 100, 250, 500] as const;
export const DEFAULT_PAGE = 100;
const BUCKETS = ['attention', 'active', 'offline'] as const;
const DEVICE_SORTS = ['status', 'name', 'site', 'age'] as const;
const INTERFACE_SORTS = ['priority', 'name', 'traffic', 'utilization'] as const;
const ADMIN_STATES = ['enabled', 'maintenance', 'disabled', 'retired'] as const;
const LIFECYCLES = ['active', 'inactive', 'stale', 'retired'] as const;
const FRESHNESS = ['fresh', 'stale', 'never'] as const;

/**
 * Return a value if it is one of the allowed ones.
 *
 * @param value - The raw parameter.
 * @param allowed - Accepted values.
 * @returns The value, or `null`.
 */
function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
	return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : null;
}

/**
 * Read a UUID filter.
 *
 * @param params - The query string.
 * @param name - Parameter name.
 * @returns The UUID, or `null`.
 */
function uuid(params: Params, name: string): string | null {
	const value = params.get(name);
	return value && UUID.test(value) ? value : null;
}

/**
 * Read the page size.
 *
 * @param params - The query string.
 * @returns One of `PAGE_SIZES`, else the default.
 */
export function pageSize(params: Params): number {
	const n = Number(params.get('limit'));
	return (PAGE_SIZES as readonly number[]).includes(n) ? n : DEFAULT_PAGE;
}

/**
 * Read the device list filters (12.3, UI-005, UI-009).
 *
 * @param params - The page's query string.
 * @returns API query parameters (without the cursor).
 */
export function readDeviceFilters(params: Params): DeviceQuery {
	const buckets = params.getAll('bucket').filter((b) => oneOf(b, BUCKETS) !== null);
	const freshness = params.getAll('freshness').filter((f) => oneOf(f, FRESHNESS) !== null);
	return {
		search: (params.get('search') ?? '').slice(0, 256),
		bucket: buckets as DeviceQuery['bucket'],
		freshness: freshness as DeviceQuery['freshness'],
		collector: uuid(params, 'collector'),
		sort: oneOf(params.get('sort'), DEVICE_SORTS) ?? 'status',
		include_retired: params.get('include_retired') === 'true',
		site: uuid(params, 'site'),
		tag: uuid(params, 'tag'),
		group: uuid(params, 'group'),
		provider: params.get('provider')?.slice(0, 256) || null,
		admin_state: oneOf(params.get('admin_state'), ADMIN_STATES),
		limit: pageSize(params)
	};
}

/**
 * Read the global interface list filters (12.5).
 *
 * @param params - The page's query string.
 * @returns API query parameters (without the cursor).
 */
export function readInterfaceFilters(params: Params): InterfaceQuery {
	const wan = params.get('wan');
	return {
		search: (params.get('search') ?? '').slice(0, 256),
		sort: oneOf(params.get('sort'), INTERFACE_SORTS) ?? 'priority',
		site: uuid(params, 'site'),
		tag: uuid(params, 'tag'),
		group: uuid(params, 'group'),
		provider: params.get('provider')?.slice(0, 256) || null,
		wan: wan === 'true' ? true : wan === 'false' ? false : undefined,
		interest: oneOf(params.get('interest'), ['primary', 'secondary'] as const),
		lifecycle: oneOf(params.get('lifecycle'), LIFECYCLES),
		include_retired: params.get('include_retired') === 'true',
		unclassified: params.get('unclassified') === 'true' ? true : undefined,
		collector: uuid(params, 'collector'),
		limit: pageSize(params)
	};
}

/** Choices for the list filters' select controls, from the catalog. */
export interface FilterOptions {
	sites: { id: string; name: string }[];
	tags: { id: string; name: string }[];
	groups: { id: string; name: string }[];
	collectors: { id: string; name: string }[];
}

const DEFAULTS: Record<string, unknown> = {
	search: '',
	sort: undefined,
	include_retired: false,
	limit: DEFAULT_PAGE
};

/**
 * Write filters back to a query string, leaving out defaults and empty values.
 *
 * @param filters - Filters as read.
 * @param defaultSort - The list's default sort, left out of the URL.
 * @returns The query string without `?`.
 */
export function toQuery(filters: Record<string, unknown>, defaultSort: string): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(filters)) {
		if (value === null || value === undefined || value === DEFAULTS[name]) continue;
		if (name === 'sort' && value === defaultSort) continue;
		if (Array.isArray(value)) value.forEach((v) => search.append(name, String(v)));
		else search.set(name, String(value));
	}
	return search.toString();
}
