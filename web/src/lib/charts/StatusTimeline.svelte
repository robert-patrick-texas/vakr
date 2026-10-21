<!--
@component
An interface's admin and oper state over time (12.5 "state timeline", 12.8).

The segment table is always shown: start, end, admin and oper state, and
the number of samples, with "no data" for a stretch without samples, so a
gap is never read as "up". The chart beside it is ECharts, loaded on
demand as its own chunk (E.31 lists the reason). If the chart cannot load,
the table still says everything.

Example: `<StatusTimeline view={timeline} />`
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import type { TimelineView } from '#lib/api/types.ts';
	import { formatInstant, knownZone } from '#lib/format/time.ts';
	import { readPalette } from './uplot-options.ts';

	interface Props {
		view: TimelineView;
	}

	const { view }: Props = $props();
	const zone = $derived(knownZone(view.window.timezone) ? view.window.timezone : 'UTC');
	let host: HTMLDivElement;
	let failed = $state(false);
	let chart = $state.raw<import('./echarts-timeline.ts').Timeline | null>(null);

	/**
	 * Read colours from the stylesheet tokens for the timeline.
	 *
	 * @returns The timeline style.
	 */
	function style() {
		const css = getComputedStyle(document.documentElement);
		const v = (name: string) => css.getPropertyValue(name).trim();
		const palette = readPalette(document.documentElement);
		return {
			up: v('--green-fg') || '#145c2e',
			down: v('--red-fg') || '#a3140c',
			degraded: v('--amber-fg') || '#7a4a00',
			unknown: v('--gray-fg') || '#4a5160',
			text: palette.muted,
			grid: palette.grid,
			formatTime: (ms: number) => formatInstant(new Date(ms), zone).slice(5, 16)
		};
	}

	onMount(() => {
		let gone = false;
		import('./echarts-timeline.ts')
			.then((m) => {
				if (!gone) chart = m.createTimeline(host);
			})
			.catch(() => {
				failed = true;
			});
		return () => {
			gone = true;
			chart?.dispose();
		};
	});

	$effect(() => {
		// Draws once the chunk has loaded, and again when a refresh brings new segments.
		chart?.update(view.segments, style());
	});
</script>

<div
	class="timeline"
	role="img"
	aria-label="Admin and oper state of {view.subject} over time; the table follows"
	bind:this={host}
></div>
{#if failed}<p class="muted">
		The timeline chart could not be loaded; the table has every segment.
	</p>{/if}
<details>
	<summary>State segments ({view.segments.length})</summary>
	<table class="density-compact">
		<caption class="sr-only">State segments of {view.subject}</caption>
		<thead>
			<tr>
				<th scope="col">From</th>
				<th scope="col">To</th>
				<th scope="col">Admin</th>
				<th scope="col">Oper</th>
				<th scope="col" class="num">Samples</th>
			</tr>
		</thead>
		<tbody>
			{#each view.segments as s (s.start)}
				<tr>
					<td>{formatInstant(s.start, zone)}</td>
					<td>{formatInstant(s.end, zone)}</td>
					<td>{s.admin ?? 'no data'}</td>
					<td>{s.oper ?? 'no data'}</td>
					<td class="num">{s.samples}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</details>

<style>
	.timeline {
		width: 100%;
		height: 110px;
	}
</style>
