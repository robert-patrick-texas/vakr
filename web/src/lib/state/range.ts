/**
 * Graph ranges: the 12.6 presets and a bounded custom range (E.28, API-006).
 *
 * A range is either a preset (`1h` to `400d`) or a custom start and end.
 * Custom ranges are checked here with the same bounds the API applies (1
 * minute to 400 days, ending at most 5 minutes in the future), so the user
 * gets the reason at once; the API still refuses anything else with 422.
 * Custom times are typed as wall-clock time in the display zone and sent as
 * UTC.
 *
 * @example
 * ```ts
 * rangeParams(readRange(new URLSearchParams('range=7d'))); // { range: '7d' }
 * ```
 *
 * @packageDocumentation
 */
import type { RangePreset } from '#lib/api/types.ts';

export const PRESETS: readonly RangePreset[] = ['1h', '6h', '24h', '7d', '30d', '90d', '400d'];
export const DEFAULT_PRESET: RangePreset = '24h';
const MINUTE = 60_000;
const DAY = 86_400_000;
/** Shortest custom range (E.28). */
export const MIN_RANGE_MS = MINUTE;
/** Longest custom range (E.28). */
export const MAX_RANGE_MS = 400 * DAY;
/** How far a custom range may end in the future (E.28). */
export const MAX_FUTURE_MS = 5 * MINUTE;

/** A preset, or a custom UTC window. */
export type GraphRange = { preset: RangePreset } | { preset: null; start: string; end: string };

/**
 * Read the range from page parameters (`range`, or `start` and `end`).
 *
 * @param params - The page's query string.
 * @returns The range; the default preset when none or an invalid one is given.
 */
export function readRange(params: Pick<URLSearchParams, 'get'>): GraphRange {
	const start = params.get('start');
	const end = params.get('end');
	if (start && end && !Number.isNaN(Date.parse(start)) && !Number.isNaN(Date.parse(end))) {
		return { preset: null, start, end };
	}
	const preset = params.get('range') as RangePreset | null;
	return { preset: preset && PRESETS.includes(preset) ? preset : DEFAULT_PRESET };
}

/**
 * The API parameters of a range.
 *
 * @param range - The range.
 * @returns `{range}` or `{start, end}`.
 */
export function rangeParams(range: GraphRange): {
	range?: RangePreset;
	start?: string;
	end?: string;
} {
	return range.preset === null ? { start: range.start, end: range.end } : { range: range.preset };
}

/**
 * Check a custom range against the E.28 bounds.
 *
 * @param startMs - Start, epoch milliseconds.
 * @param endMs - End, epoch milliseconds.
 * @param nowMs - The current time.
 * @returns Why the range is refused, or `null` when it is allowed.
 */
export function checkCustom(startMs: number, endMs: number, nowMs: number): string | null {
	if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return 'Enter both times.';
	if (endMs <= startMs) return 'The end must be after the start.';
	if (endMs - startMs < MIN_RANGE_MS) return 'A range is at least 1 minute.';
	if (endMs - startMs > MAX_RANGE_MS) return 'A range is at most 400 days.';
	if (endMs > nowMs + MAX_FUTURE_MS) return 'A range may end at most 5 minutes from now.';
	return null;
}

/**
 * The UTC offset of a zone at an instant.
 *
 * @param epochMs - The instant.
 * @param zone - IANA zone.
 * @returns Milliseconds to add to UTC to get wall time in the zone.
 */
export function zoneOffset(epochMs: number, zone: string): number {
	const parts = new Intl.DateTimeFormat('en-US', {
		timeZone: zone,
		hourCycle: 'h23',
		year: 'numeric',
		month: 'numeric',
		day: 'numeric',
		hour: 'numeric',
		minute: 'numeric',
		second: 'numeric'
	}).formatToParts(new Date(epochMs));
	const f = Object.fromEntries(parts.map((p) => [p.type, Number(p.value)]));
	const wall = Date.UTC(
		f['year'] ?? 0,
		(f['month'] ?? 1) - 1,
		f['day'],
		f['hour'],
		f['minute'],
		f['second']
	);
	return wall - Math.floor(epochMs / 1000) * 1000;
}

/**
 * Convert wall-clock time typed in a zone (`YYYY-MM-DDTHH:MM`) to an instant.
 *
 * @param local - The `datetime-local` value.
 * @param zone - The display zone.
 * @returns Epoch milliseconds, or `NaN` for an invalid value.
 */
export function wallToEpoch(local: string, zone: string): number {
	const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
	if (!m) return Number.NaN;
	const guess = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
	// Two passes settle the offset across a daylight-saving change.
	const first = guess - zoneOffset(guess, zone);
	return guess - zoneOffset(first, zone);
}

/**
 * Convert an instant to wall-clock time in a zone, for a `datetime-local` input.
 *
 * @param epochMs - The instant.
 * @param zone - The display zone.
 * @returns `YYYY-MM-DDTHH:MM`.
 */
export function epochToWall(epochMs: number, zone: string): string {
	return new Date(epochMs + zoneOffset(epochMs, zone)).toISOString().slice(0, 16);
}
