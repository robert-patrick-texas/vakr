<!--
@component
One position's latency: RTT smoke, median line, and loss bars, SmokePing style (12.6, ICMP-009).

Every probe RTT of every poll is a dot (the "smoke"); the median of each
poll is a solid line that breaks where all probes were lost; loss is drawn
as bars against the right-hand percent axis. The three differ by shape,
not only colour (WEB-T09). For long windows the API sends no probes, and
the band between the lowest and highest per-poll median replaces the
smoke (E.28 #9). A table under the chart gives the counts and values.

Example: `<LatencyChart position={view.positions[0]} rtt={view.rtt_unit} window={view.window} />`
-->
<script lang="ts">
	import type uPlot from 'uplot';
	import type { PositionLatency, Unit, WindowView } from '#lib/api/types.ts';
	import { formatInstant, knownZone } from '#lib/format/time.ts';
	import UPlotCanvas from './UPlotCanvas.svelte';
	import { latencyData, lossCounts } from './latency.ts';
	import { formatValue } from './series.ts';
	import { readPalette } from './uplot-options.ts';
	import { latencyOptions } from './latency-options.ts';

	interface Props {
		position: PositionLatency;
		rtt: Unit;
		window: WindowView;
		height?: number;
	}

	const { position, rtt, window, height = 260 }: Props = $props();
	const palette = readPalette(document.documentElement);
	const zone = $derived(knownZone(window.timezone) ? window.timezone : 'UTC');
	const d = $derived(latencyData(position, rtt));
	const counts = $derived(lossCounts(d));
	const envelope = $derived(position.smoke_mode === 'median_envelope');
	// The smoke is painted by a hook, not a series, so the scale must include it explicitly.
	const smokeMax = $derived(Math.max(0, ...d.smoke.flat()));

	/**
	 * Paint every probe RTT as a dot under the median line.
	 *
	 * @param u - The chart.
	 */
	function smoke(u: uPlot): void {
		const ctx = u.ctx;
		const r = 1.6 * devicePixelRatio;
		ctx.save();
		ctx.fillStyle = palette.smoke;
		d.smoke.forEach((rtts, i) => {
			const x = u.valToPos(d.xs[i] ?? 0, 'x', true);
			for (const v of rtts) {
				ctx.beginPath();
				ctx.arc(x, u.valToPos(v, 'y', true), r, 0, 2 * Math.PI);
				ctx.fill();
			}
		});
		ctx.restore();
	}

	const options = $derived(
		latencyOptions({
			position: position.position,
			rtt,
			zone,
			palette,
			height,
			envelope,
			smokeMax,
			smoke
		})
	);

	const data = $derived(
		(envelope
			? [d.xs, d.median, d.loss, d.low, d.high]
			: [d.xs, d.median, d.loss]) as uPlot.AlignedData
	);

	/**
	 * Say one poll's probes, median, and loss.
	 *
	 * @param i - Index on the time axis.
	 * @returns A sentence for the live readout.
	 */
	function readout(i: number): string {
		const x = d.xs[i];
		if (x === undefined) return '';
		const probes = (d.smoke[i] ?? []).map((v) => formatValue(v, rtt.display)).join(', ') || 'none';
		const median = d.median[i] === null ? 'no reply' : formatValue(d.median[i], rtt.display);
		return `${formatInstant(new Date(x * 1000), zone)}: median ${median}, loss ${formatValue(d.loss[i], '%')}, probes ${probes}`;
	}
</script>

<UPlotCanvas
	{options}
	{data}
	label="Latency of {position.position}: RTT smoke, median, and loss"
	{readout}
/>
<p class="muted legend-note">
	{#if envelope}
		The band spans the lowest to highest per-poll median in each bucket (no individual probes at
		this range).
	{:else}
		Dots: every probe's RTT ({d.probes - d.lostProbes} of {d.probes} probes answered; lost probes have
		no dot and count as loss).
	{/if}
	Polls with loss: {counts.lossy} of {counts.polls}; with every probe lost: {counts.dark}.
	{#if position.probes_truncated}Only part of the probes are shown (point cap).{/if}
</p>

<style>
	.legend-note {
		font-size: 0.85rem;
		margin: 0.3rem 0;
	}
</style>
