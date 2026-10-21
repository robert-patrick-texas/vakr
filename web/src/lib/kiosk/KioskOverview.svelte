<!--
@component
The overview on a kiosk display (12.2, UI-008, UI-013).

The same answer and panels as the overview page: device state, recent
events, vakr's own health, high-priority interfaces, WAN circuits, and the
top utilization and errors for the kiosk's window. Names are plain text
and the quick filters are left out, because nobody navigates a wall
display. One request per refresh (DASH-T06); a failed refresh keeps the
data with a stale note (UI-015).

Example: `<KioskOverview settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { overviewRead, type BoardSettings } from './boards.ts';
	import type { RotationPosition } from '#lib/state/kiosk.ts';
	import { completenessNotes, healthNotes, pageState } from '#lib/state/page-state.ts';
	import HealthPanel from '#lib/components/HealthPanel.svelte';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import OverviewPanels from '#lib/components/OverviewPanels.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import RankingTables from '#lib/components/RankingTables.svelte';
	import KioskHeader from './KioskHeader.svelte';

	interface Props {
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { settings, position = null, paused = false, onPause }: Props = $props();
	const query = createQuery(() => overviewRead(settings));
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

<KioskHeader
	board="overview"
	updatedAt={query.dataUpdatedAt}
	refreshMs={overviewRead(settings).refetchInterval}
	{position}
	{paused}
	{onPause}
/>
<PageStatus state={shown} label="Overview">
	{#snippet children(o)}
		<div class="grid">
			<OverviewPanels overview={o} zone={o.top_utilization.window.timezone} />
		</div>
		<section class="card health" aria-labelledby="k-health-h">
			<h2 id="k-health-h">Collectors, spool, and databases</h2>
			<HealthPanel health={o.health} />
		</section>
		<section aria-labelledby="k-hp-h">
			<h2 id="k-hp-h">High-priority interfaces</h2>
			<InterfaceTable
				caption="High-priority interfaces"
				hideCaption
				rows={o.high_priority.rows}
				compact
				showDevice
			/>
		</section>
		<section aria-labelledby="k-wan-h">
			<h2 id="k-wan-h">WAN circuits</h2>
			<InterfaceTable caption="WAN circuits" hideCaption rows={o.wan.rows} compact showDevice />
		</section>
		<section aria-labelledby="k-top-h">
			<h2 id="k-top-h">Top utilization and errors ({settings.window})</h2>
			<div class="grid wide">
				<div class="card"><RankingTables kind="utilization" view={o.top_utilization} /></div>
				<div class="card"><RankingTables kind="errors" view={o.top_errors} /></div>
			</div>
		</section>
	{/snippet}
</PageStatus>

<style>
	/* Two rankings side by side only where both fit at kiosk type sizes. */
	.wide {
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 46rem), 1fr));
	}
	.health {
		margin-top: var(--gap);
	}
</style>
