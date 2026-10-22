<!--
@component
A custom dashboard's widgets, in order, as one `/view` answer resolved them (UI-004, DASH-T06).

The dashboard page and the kiosk display both render through this. Pinned
interfaces show a table and, when the widget asks for them, lazy graph
cards: at most `CARDS_PER_PAGE` across the whole page (UI-016, E.31), the
first live interfaces in order. Selections use the interface and device
tables of the normal pages, say how many more matched, and name their
filters; rankings use the rankings tables. Under the kiosk layout every
link renders as text (`Link`).

Example: `<DashboardWidgets view={answer} />`
-->
<script lang="ts">
	import type { DashboardView, InterfaceListRow, WidgetView } from '#lib/api/types.ts';
	import DeviceTable from '#lib/components/DeviceTable.svelte';
	import InterfaceGraphCard from '#lib/components/InterfaceGraphCard.svelte';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import RankingTables from '#lib/components/RankingTables.svelte';
	import { completenessNotes } from '#lib/state/page-state.ts';
	import PinnedTable from './PinnedTable.svelte';

	interface Props {
		view: DashboardView;
	}

	const { view }: Props = $props();
	/** Graph cards on one page (UI-016, Appendix E.31). */
	const CARDS_PER_PAGE = 12;
	const RANGE = { range: '24h' as const };
	const cards = $derived(allocate(view.widgets));

	/**
	 * Share the page's graph cards among pinned widgets, first come first served.
	 *
	 * @param widgets - The widgets in order.
	 * @returns For each widget, the rows that get a card.
	 */
	function allocate(widgets: WidgetView[]): InterfaceListRow[][] {
		let left = CARDS_PER_PAGE;
		return widgets.map((w) => {
			if (w.kind !== 'pinned_interfaces' || !w.graphs) return [];
			const live = w.entries.flatMap((e) => (e.row ? [e.row] : []));
			const take = live.slice(0, left);
			left -= take.length;
			return take;
		});
	}

	/**
	 * Say how many matches a selection did not show.
	 *
	 * @param more - Matches not shown.
	 * @param total - Matches in all.
	 * @returns A sentence, or an empty string when everything is shown.
	 */
	function moreNote(more: number, total: number): string {
		return more > 0 ? `Showing ${total - more} of ${total}; ${more} more not shown.` : '';
	}
</script>

{#each view.widgets as w, n (n)}
	<section class="widget card" aria-labelledby="widget-{n}" data-widget={w.kind}>
		<h2 id="widget-{n}">{w.title}</h2>
		{#if w.kind === 'pinned_interfaces'}
			<PinnedTable title={w.title} entries={w.entries} />
			{#if (cards[n] ?? []).length > 0}
				<div class="cards">
					{#each cards[n] ?? [] as row (row.ref.interface_id)}
						<InterfaceGraphCard {row} range={RANGE} />
					{/each}
				</div>
			{/if}
		{:else if w.kind === 'interface_selection'}
			<p class="muted">Interfaces: {w.criteria.text}.</p>
			{#if w.rows.length === 0}
				<p>No interface matches.</p>
			{:else}
				<InterfaceTable rows={w.rows} caption={w.title} hideCaption showDevice compact />
			{/if}
			{#if w.more > 0}<p class="muted" data-testid="widget-more">
					{moreNote(w.more, w.total)}
				</p>{/if}
		{:else if w.kind === 'device_selection'}
			<p class="muted">
				Devices: {w.criteria.text} · {w.counts.attention} attention, {w.counts.active} active,
				{w.counts.offline} offline.
			</p>
			{#if w.rows.length === 0}
				<p>No device matches.</p>
			{:else}
				<DeviceTable rows={w.rows} caption={w.title} compact />
			{/if}
			{#if w.more > 0}<p class="muted" data-testid="widget-more">
					{moreNote(w.more, w.total)}
				</p>{/if}
		{:else if w.utilization}
			<p class="muted">Ranked among: {w.criteria.text}.</p>
			{#each completenessNotes(w.utilization, 'ranking') as note (note)}<p class="muted">
					{note}
				</p>{/each}
			<RankingTables kind="utilization" view={w.utilization} limit={w.utilization.rows.length} />
		{:else if w.errors}
			<p class="muted">Ranked among: {w.criteria.text}.</p>
			{#each completenessNotes(w.errors, 'ranking') as note (note)}<p class="muted">
					{note}
				</p>{/each}
			<RankingTables kind="errors" view={w.errors} limit={w.errors.rows.length} />
		{/if}
	</section>
{/each}

<style>
	.widget {
		margin-bottom: var(--gap);
	}
	.cards {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr));
		margin-top: var(--gap);
	}
</style>
