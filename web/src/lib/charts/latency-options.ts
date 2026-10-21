/**
 * uPlot options for the latency graph: median line, loss bars, optional median envelope
 * (12.6, ICMP-009; ADR-035 #8).
 *
 * The smoke (every probe's RTT) is painted by a draw hook rather than a
 * series, so it never appears as a line and the legend stays short; the
 * RTT scale is widened to include the highest probe. Loss uses its own
 * 0–100 % scale on the right. Only `src/lib/charts/` imports uPlot.
 *
 * @packageDocumentation
 */
import uPlot from 'uplot';
import type { Unit } from '#lib/api/types.ts';
import { formatInstant } from '#lib/format/time.ts';
import { formatValue } from './series.ts';
import { timeAxis, withAlpha, zonedDate, type Palette } from './uplot-options.ts';

/** What the latency options depend on. */
export interface LatencyOptionInput {
	position: string;
	rtt: Unit;
	zone: string;
	palette: Palette;
	height: number;
	envelope: boolean;
	smokeMax: number;
	smoke: (u: uPlot) => void;
}

/**
 * The series after the time column: median, loss, and the envelope bounds when used.
 *
 * @param input - The chart's inputs.
 * @returns uPlot series options.
 */
function latencySeries(input: LatencyOptionInput): uPlot.Series[] {
	const { palette, rtt } = input;
	const bounds: uPlot.Series[] = input.envelope
		? [
				{ label: 'Lowest median', stroke: palette.muted, width: 1, dash: [3, 3], spanGaps: false },
				{ label: 'Highest median', stroke: palette.muted, width: 1, dash: [3, 3], spanGaps: false }
			]
		: [];
	return [
		{
			label: `Median RTT (${input.position})`,
			stroke: palette.inbound,
			width: 2,
			spanGaps: false,
			value: (_u, v) => formatValue(v, rtt.display)
		},
		{
			label: 'Loss (bars, right axis)',
			scale: 'loss',
			stroke: palette.outbound,
			fill: withAlpha(palette.outbound, 0.35),
			width: 1,
			paths: uPlot.paths.bars?.({ size: [0.7, 10] }),
			points: { show: false },
			value: (_u, v) => formatValue(v, '%')
		},
		...bounds
	];
}

/**
 * Build the latency chart's options.
 *
 * @param input - The chart's inputs.
 * @returns Options without width.
 */
export function latencyOptions(input: LatencyOptionInput): Omit<uPlot.Options, 'width'> {
	const { palette, zone } = input;
	const grid = { stroke: palette.grid, width: 1 };
	return {
		height: input.height,
		tzDate: zonedDate(zone),
		legend: { show: true, live: true },
		cursor: { drag: { x: false, y: false } },
		scales: {
			x: { time: true },
			y: { range: (_u, _min, max) => [0, Math.max(max ?? 0, input.smokeMax) * 1.1 || 1] },
			loss: { range: [0, 100] }
		},
		axes: [
			timeAxis(palette, zone),
			{ label: `RTT (${input.rtt.display})`, stroke: palette.muted, grid, size: 60 },
			{ label: 'Loss %', scale: 'loss', side: 1, stroke: palette.muted, grid: { show: false } }
		],
		series: [
			{ value: (_u, v) => (v === null ? '—' : formatInstant(new Date(v * 1000), zone)) },
			...latencySeries(input)
		],
		bands: input.envelope ? [{ series: [4, 3], fill: withAlpha(palette.inbound, 0.15) }] : [],
		hooks: { drawClear: input.envelope ? [] : [input.smoke] }
	};
}
