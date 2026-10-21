/**
 * Query options for every list and detail read of API v1 (ADR-035 decisions 5, 6, 7).
 *
 * Each function returns TanStack Query options: a key that names the
 * request exactly, the typed `openapi-fetch` call, and the E.31 refresh
 * interval. `keepPreviousData` keeps the last answer on screen while a new
 * page or filter loads, so tables do not flash (UI-013). Every list is the
 * API's own page (API-005); nothing here asks for an unbounded list.
 *
 * @example
 * ```ts
 * const devices = createQuery(() => devicesQuery({ search: 'core', limit: 100 }));
 * ```
 *
 * @packageDocumentation
 */
import { keepPreviousData } from '@tanstack/svelte-query';
import { api, unwrap } from '#lib/api/client.ts';
import type { operations } from '#lib/api/schema.d.ts';
import type { RankingWindow } from '#lib/api/types.ts';
import { REFRESH } from './policy.ts';

type Query<Op extends keyof operations> = NonNullable<operations[Op]['parameters']['query']>;
export type DeviceQuery = Query<'devices_api_v1_devices_get'>;
export type InterfaceQuery = Query<'interfaces_api_v1_interfaces_get'>;
export type DeviceInterfaceQuery =
	Query<'device_interfaces_api_v1_devices__device_uuid__interfaces_get'>;
export type RankingQuery = Query<'top_utilization_api_v1_rankings_utilization_get'>;
export type CircuitQuery = Query<'wan_circuits_api_v1_wan_circuits_get'>;

/**
 * The overview dashboard (12.2, UI-008).
 *
 * @param window - Ranking window for top utilization and errors.
 * @returns Query options.
 */
export function overviewQuery(window: RankingWindow) {
	return {
		queryKey: ['overview', window] as const,
		queryFn: () => unwrap(api.GET('/api/v1/overview', { params: { query: { window } } })),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}

/** The overview answer as the client receives it. */
export type OverviewAnswer = Awaited<ReturnType<ReturnType<typeof overviewQuery>['queryFn']>>;

/**
 * One page of the device list (12.3, UI-005, UI-009).
 *
 * @param query - Filters, sort, page size, and cursor.
 * @returns Query options.
 */
export function devicesQuery(query: DeviceQuery) {
	return {
		queryKey: ['devices', query] as const,
		queryFn: () => unwrap(api.GET('/api/v1/devices', { params: { query } })),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}

/**
 * The device screen (12.4, UI-003).
 *
 * @param id - Device UUID.
 * @returns Query options.
 */
export function deviceQuery(id: string) {
	return {
		queryKey: ['device', id] as const,
		queryFn: () =>
			unwrap(api.GET('/api/v1/devices/{device_uuid}', { params: { path: { device_uuid: id } } })),
		refetchInterval: REFRESH.detail
	};
}

/**
 * One page of a device's interfaces, sticky, WAN, and high-priority first (UI-006, UI-017).
 *
 * @param id - Device UUID.
 * @param query - Search, sort, page size, and cursor.
 * @returns Query options.
 */
export function deviceInterfacesQuery(id: string, query: DeviceInterfaceQuery) {
	return {
		queryKey: ['device-interfaces', id, query] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/devices/{device_uuid}/interfaces', {
					params: { path: { device_uuid: id }, query }
				})
			),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}

/**
 * One page of the global interface list (12.5).
 *
 * @param query - Filters, sort, page size, and cursor.
 * @returns Query options.
 */
export function interfacesQuery(query: InterfaceQuery) {
	return {
		queryKey: ['interfaces', query] as const,
		queryFn: () => unwrap(api.GET('/api/v1/interfaces', { params: { query } })),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}

/**
 * The interface detail: identity, annotations, current values (UI-002, 12.5).
 *
 * @param id - Interface UUID.
 * @returns Query options.
 */
export function interfaceQuery(id: string) {
	return {
		queryKey: ['interface', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/interfaces/{interface_uuid}', { params: { path: { interface_uuid: id } } })
			),
		refetchInterval: REFRESH.detail
	};
}

/**
 * An interface's identity history (12.5: prior `ifIndex` values).
 *
 * @param id - Interface UUID.
 * @returns Query options (first page; 100 decisions).
 */
export function identityHistoryQuery(id: string) {
	return {
		queryKey: ['identity-history', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/interfaces/{interface_uuid}/identity-history', {
					params: { path: { interface_uuid: id }, query: { limit: 100 } }
				})
			),
		refetchInterval: REFRESH.reviews
	};
}

/**
 * Top N by the higher directional p95 utilization (DASH-003).
 *
 * @param query - Window, rows, and interface filters.
 * @returns Query options.
 */
export function utilizationRankingQuery(query: RankingQuery) {
	return {
		queryKey: ['ranking', 'utilization', query] as const,
		queryFn: () => unwrap(api.GET('/api/v1/rankings/utilization', { params: { query } })),
		refetchInterval: REFRESH.ranking,
		placeholderData: keepPreviousData
	};
}

/**
 * Top N by valid error deltas (DASH-004).
 *
 * @param query - Window, rows, and interface filters.
 * @returns Query options.
 */
export function errorRankingQuery(query: RankingQuery) {
	return {
		queryKey: ['ranking', 'errors', query] as const,
		queryFn: () => unwrap(api.GET('/api/v1/rankings/errors', { params: { query } })),
		refetchInterval: REFRESH.ranking,
		placeholderData: keepPreviousData
	};
}

/**
 * One page of primary-interest interfaces (UI-008 high-priority panel).
 *
 * @param cursor - Page cursor.
 * @param limit - Page size.
 * @returns Query options.
 */
export function highPriorityQuery(cursor: string | null, limit: number) {
	return {
		queryKey: ['high-priority', cursor, limit] as const,
		queryFn: () =>
			unwrap(api.GET('/api/v1/high-priority', { params: { query: { cursor, limit } } })),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}

/**
 * One page of WAN circuits (DASH-002, UI-008 WAN panel).
 *
 * @param query - Filters, page size, and cursor.
 * @returns Query options.
 */
export function wanQuery(query: CircuitQuery) {
	return {
		queryKey: ['wan', query] as const,
		queryFn: () => unwrap(api.GET('/api/v1/wan-circuits', { params: { query } })),
		refetchInterval: REFRESH.list,
		placeholderData: keepPreviousData
	};
}
