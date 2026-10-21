/**
 * The smoke shows every answered probe; lost probes are counted, never drawn as 0 (WEB-T05, ICMP-009).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import type { PositionLatency, Series, Unit } from '#lib/api/types.ts';
import { latencyData, lossCounts } from './latency.ts';

const MS: Unit = { base: 'us', display: 'ms', divisor: 1000 };
const T = (m: number) => new Date(Date.UTC(2026, 9, 20, 12, m)).toISOString();

/**
 * A series of one metric.
 *
 * @param metric - Metric name.
 * @param values - Per-minute values.
 * @returns The series.
 */
function s(metric: string, values: (number | null)[]): Series {
	return {
		metric,
		label: metric,
		collector_id: 'c',
		position: 'east',
		gaps: 0,
		truncated: false,
		resolutions: [],
		markers: [],
		points: values.map((value, m) => ({ t: T(m), value, low: null, high: null, flags: [] }))
	};
}

const position: PositionLatency = {
	collector_id: 'c',
	position: 'east',
	smoke_mode: 'probes',
	probes_truncated: false,
	probes: [
		{ t: T(0), rtts_us: [900, 1000, 1100, 1000, 1000], median_us: 1000, loss_pct: 0, flags: [] },
		{ t: T(1), rtts_us: [900, null, 1100, null, 1000], median_us: 1000, loss_pct: 40, flags: [] },
		{ t: T(2), rtts_us: [null, null, null, null, null], median_us: null, loss_pct: 100, flags: [] }
	],
	median: s('rtt_median_us', [1000, 1000, null]),
	loss: s('loss_pct', [0, 40, 100]),
	jitter: s('jitter_us', [50, 60, null])
};

describe('WEB-T05: latencyData', () => {
	it('draws each answered probe and leaves lost ones out of the smoke', () => {
		const d = latencyData(position, MS);
		expect(d.smoke.map((p) => p.length)).toEqual([5, 3, 0]);
		expect(d.smoke[1]).toEqual([0.9, 1.1, 1]);
		expect(d.probes).toBe(15);
		expect(d.lostProbes).toBe(7);
	});

	it('breaks the median where every probe was lost and keeps the loss values exact', () => {
		const d = latencyData(position, MS);
		expect(d.median).toEqual([1, 1, null]);
		expect(d.loss).toEqual([0, 40, 100]);
	});

	it('counts polls with some loss and with total loss', () => {
		expect(lossCounts(latencyData(position, MS))).toEqual({ polls: 3, lossy: 2, dark: 1 });
	});

	it('uses the median envelope bounds in long windows', () => {
		const median = s('rtt_median_us', [1000]);
		median.points = median.points.map((p) => ({ ...p, low: 800, high: 1500 }));
		const d = latencyData({ ...position, smoke_mode: 'median_envelope', probes: [], median }, MS);
		expect(d.low[0]).toBe(0.8);
		expect(d.high[0]).toBe(1.5);
	});
});
