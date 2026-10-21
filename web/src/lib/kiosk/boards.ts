/**
 * What each kiosk dashboard reads from API v1, and how often (UI-013, DASH-T06, DASH-010).
 *
 * A dashboard's reads are the same bounded queries its normal page makes:
 * the overview (one request), the high-priority interfaces (one page of
 * 100), the WAN circuits (one page), or the two rankings (10 rows each).
 * Each refresh therefore makes one or two requests, whatever the fleet
 * size. A kiosk `refresh` overrides each read's E.31 interval; otherwise
 * every dashboard keeps its own. The rotation uses the same options to
 * fetch the next dashboard just before it is shown, so the first read
 * happens a few seconds early rather than twice.
 *
 * @example
 * ```ts
 * const reads = boardReads('rankings', { refreshMs: null, window: '1h' });
 * reads.length; // 2
 * ```
 *
 * @packageDocumentation
 */
import {
	errorRankingQuery,
	highPriorityQuery,
	overviewQuery,
	utilizationRankingQuery,
	wanQuery
} from '#lib/query/reads.ts';
import type { KioskBoard, KioskWindow } from '#lib/state/kiosk.ts';

/** Rows of each kiosk ranking. */
export const KIOSK_RANKING_ROWS = 10;
/** High-priority interfaces on the kiosk page: the API's default page. */
export const KIOSK_HIGH_PRIORITY_ROWS = 100;

/** The options every kiosk read needs. */
export interface BoardSettings {
	/** Milliseconds between refreshes, or null for the dashboard's own E.31 interval. */
	refreshMs: number | null;
	window: KioskWindow;
}

/**
 * Apply a kiosk refresh interval to one read.
 *
 * @param options - The page's query options.
 * @param refreshMs - The kiosk interval, or null to keep the page's.
 * @returns The options with the interval.
 */
function every<T extends { refetchInterval: number }>(options: T, refreshMs: number | null): T {
	return refreshMs === null ? options : { ...options, refetchInterval: refreshMs };
}

/**
 * The overview's read.
 *
 * @param s - Kiosk settings.
 * @returns Query options.
 */
export function overviewRead(s: BoardSettings) {
	return every(overviewQuery(s.window), s.refreshMs);
}

/**
 * The high-priority interfaces' read.
 *
 * @param s - Kiosk settings.
 * @returns Query options.
 */
export function highPriorityRead(s: BoardSettings) {
	return every(highPriorityQuery(null, KIOSK_HIGH_PRIORITY_ROWS), s.refreshMs);
}

/**
 * The WAN circuits' read.
 *
 * @param s - Kiosk settings.
 * @returns Query options.
 */
export function wanRead(s: BoardSettings) {
	return every(wanQuery({}), s.refreshMs);
}

/**
 * The two rankings' reads.
 *
 * @param s - Kiosk settings.
 * @returns Utilization, then errors.
 */
export function rankingReads(s: BoardSettings) {
	const request = { window: s.window, limit: KIOSK_RANKING_ROWS };
	return [
		every(utilizationRankingQuery(request), s.refreshMs),
		every(errorRankingQuery(request), s.refreshMs)
	] as const;
}

/** Any one of the reads above, as `prefetchQuery` takes it. */
export type BoardRead =
	| ReturnType<typeof overviewRead>
	| ReturnType<typeof highPriorityRead>
	| ReturnType<typeof wanRead>
	| ReturnType<typeof rankingReads>[number];

/** A read as the rotation fetches it ahead: its key and request, nothing else. */
export interface AheadRead {
	queryKey: readonly unknown[];
	queryFn: () => Promise<unknown>;
}

/**
 * The reads to make just before a dashboard is shown, so it appears with its data.
 *
 * @param board - The dashboard that comes next.
 * @param s - Kiosk settings.
 * @returns Keys and requests of `boardReads`.
 */
export function aheadReads(board: KioskBoard, s: BoardSettings): AheadRead[] {
	return boardReads(board, s).map((r) => ({ queryKey: r.queryKey, queryFn: r.queryFn }));
}

/**
 * Every read a dashboard makes at one refresh (DASH-T06: one or two, never per interface).
 *
 * @param board - The dashboard.
 * @param s - Kiosk settings.
 * @returns Its query options.
 */
export function boardReads(board: KioskBoard, s: BoardSettings): BoardRead[] {
	switch (board) {
		case 'overview':
			return [overviewRead(s)];
		case 'high-priority':
			return [highPriorityRead(s)];
		case 'wan':
			return [wanRead(s)];
		case 'rankings':
			return [...rankingReads(s)];
	}
}
