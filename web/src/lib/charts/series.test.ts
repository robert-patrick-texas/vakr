/**
 * Graph data never invents values: gaps stay gaps, alignment holes stay holes (UI-010, WEB-T04).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import type { Series, Unit } from '#lib/api/types.ts';
import { alignSeries, formatValue, markerBands, resolutionText, summarize } from './series.ts';

const MBIT: Unit = { base: 'bit/s', display: 'Mbit/s', divisor: 1e6 };

/**
 * Build a series from (minute, value) pairs.
 *
 * @param metric - Metric name.
 * @param points - Minutes after 12:00 UTC and values; `null` is a gap point.
 * @param position - Collector position.
 * @returns The series.
 */
function series(metric: string, points: [number, number | null][], position = 'east'): Series {
	return {
		metric,
		label: `${metric} (${position})`,
		collector_id: `c-${position}`,
		position,
		gaps: points.filter(([, v]) => v === null).length,
		truncated: false,
		resolutions: [{ source: 'native', start: '', end: '', bucket_seconds: null }],
		markers: [],
		points: points.map(([m, value]) => ({
			t: new Date(Date.UTC(2026, 9, 20, 12, m)).toISOString(),
			value,
			low: null,
			high: null,
			flags: []
		}))
	};
}

describe('WEB-T04, UI-010: alignSeries', () => {
	it('keeps an API null as null (a gap), never 0', () => {
		const { xs, ys } = alignSeries(
			[
				series('in_bps', [
					[0, 2e6],
					[1, null],
					[2, 4e6]
				])
			],
			MBIT
		);
		expect(xs).toHaveLength(3);
		expect(ys[0]).toEqual([2, null, 4]);
		expect(ys[0]).not.toContain(0);
	});

	it('leaves alignment holes undefined so positions polled seconds apart stay continuous', () => {
		const east = series(
			'in_bps',
			[
				[0, 1e6],
				[2, 1e6]
			],
			'east'
		);
		const west = series('in_bps', [[1, 3e6]], 'west');
		const { xs, ys } = alignSeries([east, west], MBIT);
		expect(xs).toHaveLength(3);
		expect(ys[0]).toEqual([1, undefined, 1]);
		expect(ys[1]).toEqual([undefined, 3, undefined]);
	});

	it('keeps a real measured zero as 0', () => {
		const { ys } = alignSeries([series('in_bps', [[0, 0]])], MBIT);
		expect(ys[0]).toEqual([0]);
	});
});

describe('UI-010: markers, summaries, resolution', () => {
	it('turns markers into bands in seconds, earliest first', () => {
		const s = series('in_bps', [[0, 1]]);
		s.markers = [
			{ kind: 'restart', start: '2026-10-20T12:10:00Z', end: '2026-10-20T12:11:00Z', text: 'r' },
			{ kind: 'gap', start: '2026-10-20T12:00:00Z', end: '2026-10-20T12:05:00Z', text: 'g' }
		];
		const bands = markerBands([s]);
		expect(bands.map((b) => b.kind)).toEqual(['gap', 'restart']);
		const first = bands[0];
		expect(first ? first.end - first.start : null).toBe(300);
	});

	it('counts missing points instead of averaging them as zero', () => {
		const sum = summarize(
			series('in_bps', [
				[0, 2e6],
				[1, null],
				[2, 4e6]
			]),
			MBIT
		);
		expect(sum).toMatchObject({ points: 3, missing: 1, min: 2, max: 4, mean: 3, last: 4 });
	});

	it('has no statistics when every point is missing', () => {
		const sum = summarize(series('in_bps', [[0, null]]), MBIT);
		expect(sum.mean).toBeNull();
		expect(formatValue(sum.mean, 'Mbit/s')).toBe('—');
	});

	it('names native samples and rollup buckets', () => {
		expect(resolutionText([{ source: 'native', start: '', end: '', bucket_seconds: null }])).toBe(
			'native samples'
		);
		expect(
			resolutionText([
				{ source: 'native', start: '', end: '', bucket_seconds: 300 },
				{ source: 'rollup', start: '', end: '', bucket_seconds: 900 }
			])
		).toBe('native, 5 min buckets, then 15 min rollup buckets');
		expect(resolutionText([])).toBe('no data in this window');
	});

	it('formats values with sensible precision', () => {
		expect(formatValue(123.4, 'Mbit/s')).toBe('123 Mbit/s');
		expect(formatValue(1.234, 'ms')).toBe('1.23 ms');
		expect(formatValue(undefined, 'ms')).toBe('—');
	});
});
