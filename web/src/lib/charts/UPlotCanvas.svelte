<!--
@component
The one place a uPlot chart is mounted, resized, updated, and destroyed (ADR-035 #8, #10).

Pages never import uPlot; they use `TimeSeriesChart` or `LatencyChart`,
which build the options and data and pass them here. A refresh with the
same series updates the data in place (`setData`), so the chart does not
flash and the cursor stays (UI-013). The canvas has a text alternative
(`label`). A native range control under it moves the cursor point by
point; its value text says the time and every value at that point, so
keyboard and screen-reader users get the cursor time and values (12.6,
12.8). Mouse users can hover as usual.

Example: `<UPlotCanvas {options} {data} label="Traffic of sw10 Gi0/0/1" readout={fmt} />`
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';

	interface Props {
		/** Options without width; the canvas takes its container's width. */
		options: Omit<uPlot.Options, 'width'>;
		data: uPlot.AlignedData;
		/** Text alternative of the whole chart. */
		label: string;
		/** Words for the point at data index `i`. */
		readout: (i: number) => string;
		/** Hide the cursor control (compact cards link to the full graph instead). */
		compact?: boolean;
	}

	const { options, data, label, readout, compact = false }: Props = $props();
	let host: HTMLDivElement;
	let plot: uPlot | null = null;
	let index = $state(0);
	let touched = $state(false);
	let signature = '';
	const count = $derived(data[0].length);
	const valueText = $derived(touched && count > 0 ? readout(Math.min(index, count - 1)) : label);

	/**
	 * Identify the series layout, so a refresh with the same layout only swaps data.
	 *
	 * @param o - Options.
	 * @returns Labels, dash patterns, and scales joined.
	 */
	function layoutOf(o: Omit<uPlot.Options, 'width'>): string {
		return JSON.stringify(o.series.map((s) => [s.label, s.dash, s.scale]));
	}

	/**
	 * Create (or recreate) the chart at the container's width.
	 */
	function build(): void {
		plot?.destroy();
		const width = Math.max(host.clientWidth, 200);
		plot = new uPlot({ ...options, width }, data, host);
		signature = layoutOf(options);
	}

	onMount(() => {
		build();
		const resize = new ResizeObserver(() => {
			if (plot && Math.abs(plot.width - host.clientWidth) > 2) {
				plot.setSize({ width: Math.max(host.clientWidth, 200), height: options.height });
			}
		});
		resize.observe(host);
		return () => {
			resize.disconnect();
			plot?.destroy();
			plot = null;
		};
	});

	$effect(() => {
		if (!plot) return;
		if (layoutOf(options) === signature) plot.setData(data);
		else build();
	});

	/**
	 * Put the chart's cursor on the point the range control chose.
	 */
	function move(): void {
		touched = true;
		const x = data[0][index];
		if (!plot || x === undefined) return;
		plot.setCursor({ left: plot.valToPos(x, 'x'), top: plot.bbox.height / devicePixelRatio / 2 });
	}
</script>

<div class="canvas" role="img" aria-label={label} bind:this={host}></div>
{#if !compact && count > 0}
	<label class="cursor muted">
		<span>Read values point by point</span>
		<input
			type="range"
			min="0"
			max={count - 1}
			step="1"
			bind:value={index}
			oninput={move}
			aria-valuetext={valueText}
		/>
	</label>
{/if}

<style>
	.canvas {
		width: 100%;
		min-width: 0;
	}
	.canvas :global(.u-legend) {
		font-size: 0.85rem;
		text-align: left;
	}
	.cursor {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		font-size: 0.85rem;
	}
	.cursor input {
		flex: 1;
		max-width: 30rem;
	}
</style>
