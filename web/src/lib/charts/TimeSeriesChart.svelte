<!--
@component
A traffic, packets, errors, or utilization graph from one `GraphView` (12.6, UI-002, UI-010).

The chart is uPlot (through `UPlotCanvas`). Below it, `GraphDetails` gives
what a chart alone cannot: the resolution of every series (native samples
or rollup buckets), the display zone, the marker stretches in words, a
table of minimum, mean, maximum, and last value, and the bounded export
links. `compact` draws a small card chart for the device's interface graph
view (UI-016) with the same gap rules.

Example: `<TimeSeriesChart view={graph} height={260} />`
-->
<script lang="ts">
	import type uPlot from 'uplot';
	import type { GraphView } from '#lib/api/types.ts';
	import { formatInstant, knownZone } from '#lib/format/time.ts';
	import UPlotCanvas from './UPlotCanvas.svelte';
	import { alignSeries, formatValue, markerBands } from './series.ts';
	import {
		bandHook,
		readPalette,
		referenceHook,
		seriesStyle,
		timeAxis,
		zonedDate,
		type Palette
	} from './uplot-options.ts';

	interface Props {
		view: GraphView;
		height?: number;
		compact?: boolean;
		/** Draw capacity and contracted-rate lines, extending the scale to them (12.6). */
		showReferences?: boolean;
	}

	const { view, height = 260, compact = false, showReferences = false }: Props = $props();
	const palette: Palette = readPalette(document.documentElement);
	const zone = $derived(knownZone(view.window.timezone) ? view.window.timezone : 'UTC');
	const aligned = $derived(alignSeries(view.series, view.unit));
	const positions = $derived([...new Set(view.series.map((s) => s.position))]);
	const refs = $derived(
		showReferences
			? view.references.map((r) => ({ value: r.value / view.unit.divisor, label: r.label }))
			: []
	);

	const options = $derived.by((): Omit<uPlot.Options, 'width'> => {
		const top = refs.length > 0 ? Math.max(...refs.map((r) => r.value)) * 1.05 : null;
		return {
			height,
			tzDate: zonedDate(zone),
			legend: { show: !compact, live: true },
			cursor: { drag: { x: false, y: false } },
			scales: {
				x: { time: true },
				y: {
					range: (_u, min, max) => [Math.min(0, min ?? 0), Math.max(max ?? 1, top ?? 0) || 1]
				}
			},
			axes: [
				timeAxis(palette, zone),
				{
					label: compact ? undefined : view.unit.display,
					stroke: palette.muted,
					grid: { stroke: palette.grid, width: 1 },
					size: compact ? 40 : 60
				}
			],
			series: [
				{ value: (_u, v) => (v === null ? '—' : formatInstant(new Date(v * 1000), zone)) },
				...view.series.map((s, i) => seriesStyle(s, i, positions, palette, view.unit))
			],
			hooks: {
				drawClear: [bandHook(markerBands(view.series), palette)],
				draw: refs.length > 0 ? [referenceHook(refs, palette)] : []
			}
		};
	});

	const data = $derived([aligned.xs, ...aligned.ys] as uPlot.AlignedData);

	/**
	 * Say the time and every series' value at one point.
	 *
	 * @param i - Index on the shared time axis.
	 * @returns A sentence for the live readout.
	 */
	function readout(i: number): string {
		const x = aligned.xs[i];
		if (x === undefined) return '';
		const values = view.series.map((s, k) => {
			const v = aligned.ys[k]?.[i];
			return `${s.label} ${v === undefined ? 'no sample' : v === null ? 'no data (gap)' : formatValue(v, view.unit.display)}`;
		});
		return `${formatInstant(new Date(x * 1000), zone)}: ${values.join(', ')}`;
	}
</script>

<UPlotCanvas {options} {data} label="{view.kind} graph of {view.subject}" {readout} />
