<!--
@component
One compact traffic graph card for the device's interface graph view (UI-016, WEB-T11).

The card asks for its graph only once it scrolls into view (an
`IntersectionObserver`), with a small point cap (`CARD_POINTS`, E.31), so a
page of cards never issues one unbounded request for every interface. The
graph follows the same rules as the full one: gaps stay gaps, inbound is
solid and outbound dashed. The current errors are shown in words, and the
title links to the interface's full history.

Example: `<InterfaceGraphCard row={row} range={{ range: '24h' }} />`
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { createQuery } from '@tanstack/svelte-query';
	import type { InterfaceListRow } from '#lib/api/types.ts';
	import TimeSeriesChart from '#lib/charts/TimeSeriesChart.svelte';
	import { interfaceHref } from '#lib/links.ts';
	import { CARD_POINTS, interfaceGraphQuery, type GraphParams } from '#lib/query/reads-history.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import Link from './Link.svelte';
	import PageStatus from './PageStatus.svelte';
	import ReadingText from './ReadingText.svelte';

	interface Props {
		row: InterfaceListRow;
		range: GraphParams;
	}

	const { row, range }: Props = $props();
	let card: HTMLElement;
	let visible = $state(false);

	onMount(() => {
		const watch = new IntersectionObserver(
			(entries) => {
				if (entries.some((e) => e.isIntersecting)) {
					visible = true;
					watch.disconnect();
				}
			},
			{ rootMargin: '200px' }
		);
		watch.observe(card);
		return () => watch.disconnect();
	});

	const query = createQuery(() =>
		interfaceGraphQuery(
			row.ref.interface_id,
			'traffic',
			{ ...range, max_points: CARD_POINTS },
			visible
		)
	);
	const shown = $derived(
		pageState(query, {
			partial: (d) => (d.available ? [] : [`Not available: ${d.reason ?? 'no telemetry'}`])
		})
	);
</script>

<article class="card graph-card" bind:this={card} data-interface={row.ref.name}>
	<h3>
		<Link href={interfaceHref(row.ref.interface_id, range)}>{row.ref.name}</Link>
		{#each row.ref.markers as m (m)}<span class="tag">{m === 'primary' ? 'High priority' : m}</span
			>{/each}
	</h3>
	{#if row.ref.alias}<p class="muted alias">{row.ref.alias}</p>{/if}
	<p class="now">
		In <ReadingText reading={row.current.in_rate} compact /> · Out
		<ReadingText reading={row.current.out_rate} compact /> · Errors
		<ReadingText reading={row.current.errors} compact />
	</p>
	{#if visible}
		<PageStatus state={shown} label="Graph">
			{#snippet children(view)}
				{#if view.available}
					<TimeSeriesChart {view} height={120} compact />
				{/if}
			{/snippet}
		</PageStatus>
	{:else}
		<p class="muted deferred" data-state="deferred">The graph loads when this card is shown.</p>
	{/if}
</article>

<style>
	.graph-card h3 {
		margin: 0;
		font-size: 0.95rem;
	}
	.alias,
	.now {
		margin: 0.1rem 0;
		font-size: 0.85rem;
	}
	.tag {
		font-size: 0.75rem;
		font-weight: 400;
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.25rem;
		margin-left: 0.3rem;
	}
</style>
