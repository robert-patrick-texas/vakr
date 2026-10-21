<!--
@component
One full interface graph with what it rests on (12.6, UI-002, UI-010; WEB-T04, WEB-T06).

It fetches the graph for the page's range, draws it, and lists the
resolution, display zone, markers, exact values, and bounded exports. A
graph the API cannot compute (no telemetry, or utilization without a
capacity) says why instead of drawing nothing. Traffic graphs can show the
capacity and contracted-rate lines (12.6).

Example: `<GraphPanel interfaceId={id} kind="traffic" params={{ range: '24h' }} title="Traffic" />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import type { GraphKind } from '#lib/api/types.ts';
	import GraphDetails from '#lib/charts/GraphDetails.svelte';
	import TimeSeriesChart from '#lib/charts/TimeSeriesChart.svelte';
	import { interfaceExportHref } from '#lib/links.ts';
	import { interfaceGraphQuery, type GraphParams } from '#lib/query/reads-history.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
	import PageStatus from './PageStatus.svelte';

	interface Props {
		interfaceId: string;
		kind: GraphKind;
		params: GraphParams;
		title: string;
	}

	const { interfaceId, kind, params, title }: Props = $props();
	let references = $state(false);
	const query = createQuery(() => interfaceGraphQuery(interfaceId, kind, params));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.available && d.series.length === 0,
			partial: (d) => completenessNotes(d, `${kind} graph`)
		})
	);
	const exports = $derived([
		{ label: 'CSV', href: interfaceExportHref(interfaceId, kind, 'csv', params) },
		{ label: 'JSON', href: interfaceExportHref(interfaceId, kind, 'json', params) }
	]);
</script>

<section class="graph" aria-labelledby="g-{kind}" data-graph={kind}>
	<h3 id="g-{kind}">{title}</h3>
	<PageStatus state={shown} label={title} emptyText="No data in this window.">
		{#snippet children(view)}
			{#if view.available}
				{#if view.references.length > 0}
					<label class="refs"
						><input type="checkbox" bind:checked={references} /> Show
						{view.references.map((r) => r.label).join(' and ')}</label
					>
				{/if}
				<TimeSeriesChart {view} showReferences={references} />
				<p class="muted legend-note">
					Inbound is a solid line over a light fill; outbound is dashed. Shaded stretches are
					quality markers; a break in a line is missing data, never zero.
				</p>
				<GraphDetails
					series={view.series}
					unit={view.unit}
					window={view.window}
					notes={view.notes}
					truncated={view.truncated}
					{exports}
				/>
			{/if}
		{/snippet}
	</PageStatus>
</section>

<style>
	.graph {
		margin-bottom: 1.2rem;
	}
	.refs,
	.legend-note {
		font-size: 0.85rem;
	}
</style>
