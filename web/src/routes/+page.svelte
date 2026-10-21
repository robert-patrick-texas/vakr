<!--
@component
The authenticated overview (12.2, UI-008).

Device state summary, vakr's own health, high-priority interfaces, WAN
circuits, top utilization and top errors for the chosen window, recent
events, and quick filters by provider, site, tag, and group. It refreshes
every 30 s in place. Telemetry or collector trouble shows as a partial
state; it never turns into devices being down (UI-014, UI-015).
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import type { RankingWindow } from '#lib/api/types.ts';
	import { overviewQuery } from '#lib/query/reads.ts';
	import { completenessNotes, healthNotes, pageState } from '#lib/state/page-state.ts';
	import HealthPanel from '#lib/components/HealthPanel.svelte';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import OverviewPanels from '#lib/components/OverviewPanels.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import RankingTables from '#lib/components/RankingTables.svelte';

	const WINDOWS: RankingWindow[] = ['15m', '1h', '6h', '24h'];
	const window = $derived(
		(WINDOWS as string[]).includes(page.url.searchParams.get('window') ?? '')
			? (page.url.searchParams.get('window') as RankingWindow)
			: '15m'
	);
	const query = createQuery(() => overviewQuery(window));
	const shown = $derived(
		pageState(query, {
			partial: (d) => [
				...healthNotes(d.health),
				...completenessNotes(d.top_utilization, 'utilization ranking'),
				...completenessNotes(d.top_errors, 'error ranking')
			]
		})
	);
</script>

<svelte:head><title>Overview · vakr</title></svelte:head>

<h1>Overview</h1>
<PageStatus state={shown} label="Overview">
	{#snippet children(o)}
		<div class="grid">
			<OverviewPanels overview={o} zone={o.top_utilization.window.timezone} />
			<section class="card" aria-labelledby="health-h">
				<h2 id="health-h">Collectors, spool, and databases</h2>
				<HealthPanel health={o.health} />
				<p><a href="/collectors">Collector details</a></p>
			</section>
		</div>

		<section aria-labelledby="hp-h">
			<h2 id="hp-h">High-priority interfaces</h2>
			<InterfaceTable
				caption="High-priority interfaces"
				hideCaption
				rows={o.high_priority.rows}
				compact
				showDevice
			/>
			{#if o.high_priority.total > o.high_priority.rows.length}
				<p><a href="/high-priority">All {o.high_priority.total} high-priority interfaces</a></p>
			{/if}
		</section>

		<section aria-labelledby="wan-h">
			<h2 id="wan-h">WAN circuits</h2>
			<InterfaceTable caption="WAN circuits" hideCaption rows={o.wan.rows} compact showDevice />
			{#if o.wan.total > o.wan.rows.length}
				<p><a href="/wan">All {o.wan.total} WAN circuits</a></p>
			{/if}
		</section>

		<section aria-labelledby="top-h">
			<h2 id="top-h">Top utilization and errors</h2>
			<div class="row" role="group" aria-label="Ranking window">
				{#each WINDOWS as w (w)}
					<button
						type="button"
						aria-pressed={w === window}
						onclick={() => goto(`/?window=${w}`, { replace: true, reset: false })}>{w}</button
					>
				{/each}
				<a href="/rankings?window={window}">Full rankings</a>
			</div>
			<div class="grid wide">
				<div class="card"><RankingTables kind="utilization" view={o.top_utilization} /></div>
				<div class="card"><RankingTables kind="errors" view={o.top_errors} /></div>
			</div>
		</section>
	{/snippet}
</PageStatus>

<style>
	.wide {
		grid-template-columns: repeat(auto-fill, minmax(32rem, 1fr));
		margin-top: 0.5rem;
	}
</style>
