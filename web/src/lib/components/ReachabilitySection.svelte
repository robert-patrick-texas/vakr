<!--
@component
A device's reachability graph: RTT smoke, median, and loss per position (12.4, 12.6, ICMP-009,
MPOS-008, WEB-T05, WEB-T06).

One chart per collector position, never merged. Under each: the
resolution, the display zone, the marker stretches, exact values, and the
bounded CSV/JSON exports for the same window.

Example: `<ReachabilitySection deviceId={id} params={{ range: '24h' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import GraphDetails from '#lib/charts/GraphDetails.svelte';
	import LatencyChart from '#lib/charts/LatencyChart.svelte';
	import { reachabilityExportHref } from '#lib/links.ts';
	import { reachabilityQuery, type GraphParams } from '#lib/query/reads-history.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
	import PageStatus from './PageStatus.svelte';

	interface Props {
		deviceId: string;
		params: GraphParams;
	}

	const { deviceId, params }: Props = $props();
	const query = createQuery(() => reachabilityQuery(deviceId, params));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.available && d.positions.length === 0,
			partial: (d) => completenessNotes(d, 'latency graph')
		})
	);
	const exports = $derived([
		{ label: 'CSV', href: reachabilityExportHref(deviceId, 'csv', params) },
		{ label: 'JSON', href: reachabilityExportHref(deviceId, 'json', params) }
	]);
</script>

<PageStatus
	state={shown}
	label="Reachability"
	emptyText="No collector has latency data in this window."
>
	{#snippet children(view)}
		{#each view.positions as position (position.collector_id)}
			<section class="position" aria-label="Latency from {position.position}">
				<h3>From {position.position}</h3>
				{#if position.median.points.length === 0 && position.probes.length === 0}
					<p class="notice notice-gray" data-state="empty">
						No latency data from {position.position} in this window.
					</p>
				{:else}
					<LatencyChart {position} rtt={view.rtt_unit} window={view.window} />
				{/if}
				<GraphDetails
					series={[position.median, position.loss, position.jitter]}
					unit={view.rtt_unit}
					unitFor={(s) => (s.metric === 'loss_pct' ? view.loss_unit : view.rtt_unit)}
					window={view.window}
					notes={view.notes}
					truncated={view.truncated}
					{exports}
				/>
			</section>
		{/each}
	{/snippet}
</PageStatus>

<style>
	.position {
		margin-bottom: 1rem;
	}
</style>
