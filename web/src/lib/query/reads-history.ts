/**
 * Query options for history graphs: traffic, packets, errors, utilization, state, latency
 * (12.6, API-006; ADR-035 decision 6).
 *
 * Every graph read is bounded by the API (a range of at most 400 days and
 * at most 1,200 points per series, E.28) and names its resolution. Compact
 * graph cards ask for fewer points (`CARD_POINTS`, E.31), so a page of
 * cards stays light. Graphs refresh every 60 s, the default poll interval.
 *
 * @example
 * ```ts
 * createQuery(() => interfaceGraphQuery(id, 'traffic', { range: '24h' }));
 * ```
 *
 * @packageDocumentation
 */
import { keepPreviousData } from '@tanstack/svelte-query';
import { api, unwrap } from '#lib/api/client.ts';
import type { GraphKind, SourceChoice } from '#lib/api/types.ts';
import type { rangeParams } from '#lib/state/range.ts';
import { REFRESH } from './policy.ts';

/** Points per series on a compact graph card (UI-016, Appendix E.31). */
export const CARD_POINTS = 120;
/** Points per series on a full graph (the API's cap, E.19). */
export const FULL_POINTS = 1200;

/** Range, source, point cap, and position of a graph request. */
export type GraphParams = ReturnType<typeof rangeParams> & {
	source?: SourceChoice;
	max_points?: number;
	collector?: string | null;
};

/**
 * Fetch one interface graph of a kind.
 *
 * @param id - Interface UUID.
 * @param kind - `traffic`, `packets`, `errors`, or `utilization`.
 * @param query - Graph parameters.
 * @returns The graph answer.
 */
function fetchGraph(id: string, kind: GraphKind, query: GraphParams) {
	const params = { path: { interface_uuid: id }, query };
	switch (kind) {
		case 'traffic':
			return unwrap(api.GET('/api/v1/interfaces/{interface_uuid}/history/traffic', { params }));
		case 'packets':
			return unwrap(api.GET('/api/v1/interfaces/{interface_uuid}/history/packets', { params }));
		case 'errors':
			return unwrap(api.GET('/api/v1/interfaces/{interface_uuid}/history/errors', { params }));
		default:
			return unwrap(api.GET('/api/v1/interfaces/{interface_uuid}/history/utilization', { params }));
	}
}

/**
 * An interface graph (UI-002, 12.6).
 *
 * @param id - Interface UUID.
 * @param kind - The graph kind.
 * @param query - Graph parameters.
 * @param enabled - False until the graph is needed (lazy cards, UI-016).
 * @returns Query options.
 */
export function interfaceGraphQuery(
	id: string,
	kind: GraphKind,
	query: GraphParams,
	enabled = true
) {
	return {
		queryKey: ['graph', id, kind, query] as const,
		queryFn: () => fetchGraph(id, kind, query),
		refetchInterval: REFRESH.graph,
		placeholderData: keepPreviousData,
		enabled
	};
}

/**
 * An interface's admin and oper state timeline (12.5).
 *
 * @param id - Interface UUID.
 * @param query - Graph parameters (the API keeps the last 7 days of the range).
 * @returns Query options.
 */
export function timelineQuery(id: string, query: GraphParams) {
	return {
		queryKey: ['timeline', id, query] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/interfaces/{interface_uuid}/history/status', {
					params: { path: { interface_uuid: id }, query }
				})
			),
		refetchInterval: REFRESH.graph,
		placeholderData: keepPreviousData
	};
}

/**
 * A device's latency, loss, jitter, and smoke, one set per position (12.6, ICMP-009, MPOS-008).
 *
 * @param id - Device UUID.
 * @param query - Graph parameters.
 * @returns Query options.
 */
export function reachabilityQuery(id: string, query: GraphParams) {
	return {
		queryKey: ['reachability', id, query] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/devices/{device_uuid}/history/reachability', {
					params: { path: { device_uuid: id }, query }
				})
			),
		refetchInterval: REFRESH.graph,
		placeholderData: keepPreviousData
	};
}
