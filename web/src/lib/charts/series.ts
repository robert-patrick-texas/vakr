/**
 * Graph answers turned into chart data without inventing values (UI-010, 12.6, WEB-T04).
 *
 * The API sends each series as points `{t, value}` where a missing or
 * invalid value is `null`, plus `markers` for gap, baseline, restart,
 * discontinuity, partial, collector, and over-capacity stretches. Charts
 * need one shared time axis. `alignSeries` builds it from the union of all
 * points and keeps two kinds of "nothing" apart:
 *
 * - `null`: the API said there is no valid value here. The line breaks
 *   (a gap); it never drops to zero.
 * - `undefined`: this series simply has no point at another series' time
 *   (two positions polled a few seconds apart). The line is drawn across it.
 *
 * Values are divided by the unit's `divisor`, so the axis shows the unit
 * the API chose (`Mbit/s`, `ms`, `%`).
 *
 * @example
 * ```ts
 * const { xs, ys } = alignSeries(view.series, view.unit);
 * ```
 *
 * @packageDocumentation
 */
import type { Marker, Resolution, Series, Unit } from '#lib/api/types.ts';

/** One time axis and one value array per series. */
export interface Aligned {
	xs: number[];
	ys: (number | null | undefined)[][];
}

/**
 * Convert an ISO 8601 UTC time to Unix seconds.
 *
 * @param iso - Such as `2026-10-20T11:20:00Z`.
 * @returns Seconds since the epoch.
 */
export function epoch(iso: string): number {
	return Date.parse(iso) / 1000;
}

/**
 * Put every series on one time axis, keeping gaps as `null` and alignment holes as `undefined`.
 *
 * @param series - The series of one graph answer.
 * @param unit - The answer's unit; values are divided by `divisor`.
 * @returns The aligned arrays, times ascending.
 */
export function alignSeries(series: readonly Series[], unit: Unit): Aligned {
	const times = new Set<number>();
	for (const s of series) for (const p of s.points) times.add(epoch(p.t));
	const xs = [...times].sort((a, b) => a - b);
	const index = new Map(xs.map((x, i) => [x, i]));
	const ys = series.map((s) => {
		const column: (number | null | undefined)[] = new Array<number | null | undefined>(xs.length);
		for (const p of s.points) {
			const i = index.get(epoch(p.t));
			if (i !== undefined) column[i] = p.value === null ? null : p.value / unit.divisor;
		}
		return column;
	});
	return { xs, ys };
}

/** A marked stretch of a graph, in Unix seconds. */
export interface Band {
	start: number;
	end: number;
	kind: Marker['kind'];
	text: string;
	series: string;
}

/**
 * Collect the quality markers of every series as time bands.
 *
 * @param series - The series.
 * @returns Bands, earliest first; a stretch shared by several series appears once per series.
 */
export function markerBands(series: readonly Series[]): Band[] {
	const bands = series.flatMap((s) =>
		s.markers.map((m) => ({
			start: epoch(m.start),
			end: epoch(m.end),
			kind: m.kind,
			text: m.text,
			series: s.label
		}))
	);
	return bands.sort((a, b) => a.start - b.start || a.series.localeCompare(b.series));
}

/** Tabular summary of one series (12.6, 12.8: exact values without the chart). */
export interface Summary {
	label: string;
	points: number;
	missing: number;
	min: number | null;
	mean: number | null;
	max: number | null;
	last: number | null;
	lastAt: string | null;
}

/**
 * Summarize one series in its display unit; missing points are counted, never averaged as zero.
 *
 * @param series - The series.
 * @param unit - Its unit.
 * @returns The summary; statistics are `null` when no point has a value.
 */
export function summarize(series: Series, unit: Unit): Summary {
	const values: number[] = [];
	let last: number | null = null;
	let lastAt: string | null = null;
	for (const p of series.points) {
		if (p.value === null) continue;
		const v = p.value / unit.divisor;
		values.push(v);
		last = v;
		lastAt = p.t;
	}
	const has = values.length > 0;
	return {
		label: series.label,
		points: series.points.length,
		missing: series.points.length - values.length,
		min: has ? Math.min(...values) : null,
		mean: has ? values.reduce((a, b) => a + b, 0) / values.length : null,
		max: has ? Math.max(...values) : null,
		last,
		lastAt
	};
}

/**
 * Describe what one series rests on: native samples or rollup buckets, per part of the window.
 *
 * @param resolutions - The series' `resolutions`.
 * @returns Such as `native samples` or `15 min rollup buckets`; joined with `then`.
 */
export function resolutionText(resolutions: readonly Resolution[]): string {
	if (resolutions.length === 0) return 'no data in this window';
	const parts = resolutions.map((r) => {
		const bucket = r.bucket_seconds;
		const width =
			bucket === null ? null : bucket >= 3600 ? `${bucket / 3600} h` : `${bucket / 60} min`;
		if (r.source === 'native')
			return width === null ? 'native samples' : `native, ${width} buckets`;
		return width === null ? `${r.source}` : `${width} ${r.source} buckets`;
	});
	return [...new Set(parts)].join(', then ');
}

/**
 * Format a value in a unit for a table cell or tooltip; missing stays a dash.
 *
 * @param value - The value already divided by the unit's divisor, or `null`.
 * @param display - The unit label, such as `Mbit/s`.
 * @returns Such as `12.3 Mbit/s`, or `—`.
 */
export function formatValue(value: number | null | undefined, display: string): string {
	if (value === null || value === undefined || !Number.isFinite(value)) return '—';
	const abs = Math.abs(value);
	const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : 3;
	return `${value.toFixed(digits)} ${display}`;
}
