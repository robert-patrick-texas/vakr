/**
 * Links between pages and to bounded exports (12.1, 12.2: widgets link to filtered views).
 *
 * vakr is always served at the origin root, so links are plain absolute
 * paths. Query strings are built with `URLSearchParams`, which encodes
 * every value; UUIDs in paths come from API answers.
 *
 * @example
 * ```ts
 * devicesHref({ site: siteId }); // '/devices?site=…'
 * ```
 *
 * @packageDocumentation
 */
import type { GraphKind } from '#lib/api/types.ts';

type Params = Record<string, unknown>;

/**
 * Build a query string from parameters, leaving out empty ones.
 *
 * @param params - Names to values; arrays repeat the name.
 * @returns `?a=1&b=2`, or an empty string.
 */
export function query(params: Params): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(params)) {
		if (value === null || value === undefined || value === '' || value === false) continue;
		if (Array.isArray(value)) value.forEach((v) => search.append(name, String(v)));
		else search.set(name, String(value));
	}
	const text = search.toString();
	return text ? `?${text}` : '';
}

/**
 * Link to a device page.
 *
 * @param id - Device UUID.
 * @returns The path.
 */
export function deviceHref(id: string): string {
	return `/devices/${encodeURIComponent(id)}`;
}

/**
 * Link to an interface page, optionally with a graph range.
 *
 * @param id - Interface UUID.
 * @param params - Extra parameters such as `range`.
 * @returns The path.
 */
export function interfaceHref(id: string, params: Params = {}): string {
	return `/interfaces/${encodeURIComponent(id)}${query(params)}`;
}

/**
 * Link to the device list with filters.
 *
 * @param params - Filters such as `site`, `tag`, `bucket`.
 * @returns The path.
 */
export function devicesHref(params: Params = {}): string {
	return `/devices${query(params)}`;
}

/**
 * Link to the interface list with filters.
 *
 * @param params - Filters such as `provider`, `wan`.
 * @returns The path.
 */
export function interfacesHref(params: Params = {}): string {
	return `/interfaces${query(params)}`;
}

/**
 * Link to a bounded interface graph export (API-012: at most 20,000 rows and 4 MiB).
 *
 * @param id - Interface UUID.
 * @param kind - Graph kind.
 * @param format - `csv` or `json`.
 * @param params - The graph's range parameters.
 * @returns The API path; the browser downloads it with the session cookie.
 */
export function interfaceExportHref(
	id: string,
	kind: GraphKind,
	format: 'csv' | 'json',
	params: Params
): string {
	const path = `/api/v1/exports/interfaces/${encodeURIComponent(id)}/history/${kind}`;
	return `${path}${query({ ...params, format })}`;
}

/**
 * Link to a bounded reachability export.
 *
 * @param id - Device UUID.
 * @param format - `csv` or `json`.
 * @param params - The graph's range parameters.
 * @returns The API path.
 */
export function reachabilityExportHref(id: string, format: 'csv' | 'json', params: Params): string {
	const path = `/api/v1/exports/devices/${encodeURIComponent(id)}/history/reachability`;
	return `${path}${query({ ...params, format })}`;
}

/**
 * Accept only same-origin paths as a place to return to after sign-in.
 *
 * @param next - The `next` parameter.
 * @returns The path, or `/` for anything that could leave the origin.
 */
export function safeNext(next: string | null): string {
	if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return '/';
	return next.startsWith('/login') ? '/' : next;
}
