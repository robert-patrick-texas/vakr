<!--
@component
Top utilization and top errors on a kiosk display (DASH-003, DASH-004, UI-013).

Ten rows of each ranking for the kiosk's window, with coverage per row,
as on the rankings page. Two requests per refresh (DASH-T06). The
freshness line names the older of the two answers.

Example: `<KioskRankings settings={{ refreshMs: null, window: '1h' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { rankingReads, type BoardSettings } from './boards.ts';
	import type { RotationPosition } from '#lib/state/kiosk.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
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
	const utilization = createQuery(() => rankingReads(settings)[0]);
	const errors = createQuery(() => rankingReads(settings)[1]);
	const util = $derived(
		pageState(utilization, { partial: (d) => completenessNotes(d, 'utilization ranking') })
	);
	const errs = $derived(
		pageState(errors, { partial: (d) => completenessNotes(d, 'error ranking') })
	);
	const oldest = $derived(
		utilization.dataUpdatedAt > 0 && errors.dataUpdatedAt > 0
			? Math.min(utilization.dataUpdatedAt, errors.dataUpdatedAt)
			: 0
	);
</script>

<KioskHeader
	board="rankings"
	updatedAt={oldest}
	refreshMs={rankingReads(settings)[0].refetchInterval}
	{position}
	{paused}
	{onPause}
/>
<div class="grid wide">
	<section class="card" aria-label="Top utilization">
		<PageStatus state={util} label="Utilization ranking">
			{#snippet children(v)}<RankingTables kind="utilization" view={v} />{/snippet}
		</PageStatus>
	</section>
	<section class="card" aria-label="Top errors">
		<PageStatus state={errs} label="Error ranking">
			{#snippet children(v)}<RankingTables kind="errors" view={v} />{/snippet}
		</PageStatus>
	</section>
</div>

<style>
	/* Side by side only where both fit at kiosk type sizes. */
	.wide {
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 46rem), 1fr));
	}
</style>
