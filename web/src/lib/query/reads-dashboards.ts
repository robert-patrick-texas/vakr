/**
 * Query options for custom dashboards: the list, a definition, and a resolved view (UI-004).
 *
 * The view is one bounded request per refresh (DASH-T06): the API resolves
 * every widget server-side. It refreshes at the dashboard's own
 * `refresh_s` once the first answer says what that is, unless a kiosk
 * display overrides it; the list and definitions do not poll.
 *
 * @example
 * ```ts
 * createQuery(() => dashboardViewQuery(id));
 * ```
 *
 * @packageDocumentation
 */
import { keepPreviousData } from '@tanstack/svelte-query';
import { api, unwrap } from '#lib/api/client.ts';
import type { DashboardView } from '#lib/api/types.ts';
import { REFRESH } from './policy.ts';

/** Dashboards the list reads at once: the API keeps at most 200 (E.31). */
export const DASHBOARD_LIST_LIMIT = 200;
const LIST_TTL = 60 * 1000;

/**
 * Every custom dashboard, by name.
 *
 * @returns Query options.
 */
export function dashboardsQuery() {
	return {
		queryKey: ['dashboards'] as const,
		queryFn: () =>
			unwrap(api.GET('/api/v1/dashboards', { params: { query: { limit: DASHBOARD_LIST_LIMIT } } })),
		staleTime: LIST_TTL
	};
}

/**
 * One dashboard's definition, for the editor.
 *
 * @param id - Dashboard UUID.
 * @returns Query options.
 */
export function dashboardQuery(id: string) {
	return {
		queryKey: ['dashboard', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/dashboards/{dashboard_uuid}', { params: { path: { dashboard_uuid: id } } })
			)
	};
}

/**
 * Milliseconds between refreshes of a view: the override, else the dashboard's own.
 *
 * @param data - The last answer, if any.
 * @param overrideMs - A kiosk `refresh`, or null.
 * @returns The interval.
 */
export function viewInterval(data: DashboardView | undefined, overrideMs: number | null): number {
	if (overrideMs !== null) return overrideMs;
	return data ? data.refresh_s * 1000 : REFRESH.list;
}

/**
 * One dashboard with every widget resolved and current values.
 *
 * @param id - Dashboard UUID.
 * @param overrideMs - A kiosk refresh interval, or null for the dashboard's `refresh_s`.
 * @returns Query options.
 */
export function dashboardViewQuery(id: string, overrideMs: number | null = null) {
	return {
		queryKey: ['dashboard-view', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/dashboards/{dashboard_uuid}/view', {
					params: { path: { dashboard_uuid: id } }
				})
			),
		refetchInterval: (query: { state: { data: DashboardView | undefined } }) =>
			viewInterval(query.state.data, overrideMs),
		placeholderData: keepPreviousData
	};
}
