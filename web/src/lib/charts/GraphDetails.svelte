<!--
@component
What a graph rests on, in words: resolution, zone, quality markers, a summary table, exports
(12.6, 12.8, UI-010, UI-015, API-012).

Every series names its source per part of the window (native samples,
native buckets, or rollup buckets). The zone line names the display zone
and the site's zone. Marker stretches are listed with the API's reason, so
a gap is explained instead of looking like zero. The summary table gives
exact values for keyboard and screen-reader users; missing points are
counted, never averaged in as zero. Exports are the API's bounded CSV and
JSON files for the same window.

Example: `<GraphDetails series={view.series} unit={view.unit} window={view.window} exports={links} />`
-->
<script lang="ts">
	import type { Series, Unit, WindowView } from '#lib/api/types.ts';
	import { formatInstant, zoneLabel } from '#lib/format/time.ts';
	import { MARKER_LETTER } from './uplot-options.ts';
	import { formatValue, markerBands, resolutionText, summarize } from './series.ts';

	interface Props {
		series: Series[];
		unit: Unit;
		/** The unit of one series, when the series differ (latency: RTT and loss). */
		unitFor?: (s: Series) => Unit;
		window: WindowView;
		/** Bounded export links (CSV and JSON). */
		exports?: { label: string; href: string }[];
		notes?: string[];
		truncated?: boolean;
	}

	const {
		series,
		unit,
		unitFor,
		window,
		exports = [],
		notes = [],
		truncated = false
	}: Props = $props();
	const zone = $derived(window.timezone);
	const bands = $derived(markerBands(series));
	const summaries = $derived(
		series.map((s) => ({
			...summarize(s, unitFor?.(s) ?? unit),
			display: (unitFor?.(s) ?? unit).display
		}))
	);
	const resolutions = $derived([
		...new Set(series.map((s) => `${s.label}: ${resolutionText(s.resolutions)}`))
	]);
</script>

<div class="details">
	<p class="disclosure muted">
		<span data-testid="zone">{zoneLabel(zone, window.site_timezone)}</span>.
		<span data-testid="resolution">Resolution: {resolutions.join('; ')}</span>.
		{#if truncated || series.some((s) => s.truncated)}
			<strong>Points were capped</strong> at the point limit; buckets are wider than the poll interval.
		{/if}
	</p>
	{#each notes as note (note)}<p class="muted note">{note}</p>{/each}

	{#if bands.length > 0}
		<details>
			<summary>Quality markers ({bands.length})</summary>
			<ul class="markers">
				{#each bands as band, i (i)}
					<li>
						<span class="letter" aria-hidden="true">{MARKER_LETTER[band.kind]}</span>
						<strong>{band.kind.replace('_', ' ')}</strong>
						{formatInstant(new Date(band.start * 1000), zone)} – {formatInstant(
							new Date(band.end * 1000),
							zone
						)}
						({band.series}): {band.text}
					</li>
				{/each}
			</ul>
		</details>
	{/if}

	<table class="summary density-compact">
		<caption class="sr-only">Summary of each series</caption>
		<thead>
			<tr>
				<th scope="col">Series</th>
				<th scope="col" class="num">Last</th>
				<th scope="col">At</th>
				<th scope="col" class="num">Min</th>
				<th scope="col" class="num">Mean</th>
				<th scope="col" class="num">Max</th>
				<th scope="col" class="num">Points (missing)</th>
			</tr>
		</thead>
		<tbody>
			{#each summaries as s (s.label)}
				<tr>
					<th scope="row">{s.label}</th>
					<td class="num">{formatValue(s.last, s.display)}</td>
					<td>{formatInstant(s.lastAt, zone)}</td>
					<td class="num">{formatValue(s.min, s.display)}</td>
					<td class="num">{formatValue(s.mean, s.display)}</td>
					<td class="num">{formatValue(s.max, s.display)}</td>
					<td class="num">{s.points} ({s.missing})</td>
				</tr>
			{/each}
		</tbody>
	</table>
	{#if exports.length > 0}
		<p class="row exports">
			Download this window:
			{#each exports as link (link.href)}<a href={link.href} download>{link.label}</a>{/each}
		</p>
	{/if}
</div>

<style>
	.disclosure,
	.note {
		font-size: 0.85rem;
		margin: 0.3rem 0;
	}
	.markers {
		font-size: 0.85rem;
		padding-left: 1.1rem;
	}
	.letter {
		font-family: var(--mono);
		border: 1px solid var(--border);
		padding: 0 0.25em;
		margin-right: 0.3em;
	}
	.summary {
		margin-top: 0.4rem;
		font-size: 0.85rem;
	}
	.exports {
		font-size: 0.85rem;
	}
</style>
