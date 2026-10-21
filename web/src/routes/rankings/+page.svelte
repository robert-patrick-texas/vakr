<!--
@component
Top utilization and top errors (DASH-003, DASH-004, DASH-005, UI-008, API-012).

The window (15 minutes to 24 hours), the number of rows (up to 100), the
position, and the interface filters live in the URL. Coverage is shown
per row; interfaces left out are listed with the reason. The 24-hour
window takes several seconds the first time (E.28 #1), and the page says
so while it waits. Exports are the API's bounded CSV and JSON files.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import type { RankingWindow } from '#lib/api/types.ts';
	import { query as qs } from '#lib/links.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import {
		errorRankingQuery,
		utilizationRankingQuery,
		type RankingQuery
	} from '#lib/query/reads.ts';
	import { readInterfaceFilters } from '#lib/state/filters.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
	import InterfaceFilters from '#lib/components/InterfaceFilters.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import RankingTables from '#lib/components/RankingTables.svelte';

	const WINDOWS: RankingWindow[] = ['15m', '1h', '6h', '24h'];
	const ROWS = [10, 25, 50, 100];
	const options = filterOptions();
	const params = $derived(page.url.searchParams);
	const window = $derived(
		WINDOWS.find((w) => w === params.get('window')) ?? ('15m' as RankingWindow)
	);
	const rows = $derived(ROWS.find((n) => String(n) === params.get('rows')) ?? 10);
	const filters = $derived(readInterfaceFilters(params));
	const request = $derived<RankingQuery>({
		window,
		limit: rows,
		collector: filters.collector,
		site: filters.site,
		tag: filters.tag,
		group: filters.group,
		provider: filters.provider,
		wan: filters.wan,
		interest: filters.interest,
		lifecycle: filters.lifecycle,
		include_retired: filters.include_retired
	});
	const utilization = createQuery(() => utilizationRankingQuery(request));
	const errors = createQuery(() => errorRankingQuery(request));

	/**
	 * Write the ranking parameters to the URL (`rows` is the ranking's length).
	 *
	 * @param patch - Changed window, rows, or filters.
	 */
	function apply(patch: Partial<RankingQuery> & { rows?: number }): void {
		const next = { ...request, ...patch, limit: null, rows: patch.rows ?? rows };
		void goto(`/rankings${qs(next)}`, { replace: true, reset: false });
	}

	/**
	 * Apply the interface filters, keeping only those a ranking takes.
	 *
	 * @param f - The filter form's values.
	 */
	function applyFilters(f: typeof filters): void {
		const { site, tag, group, provider, wan, interest, lifecycle, include_retired, collector } = f;
		apply({ site, tag, group, provider, wan, interest, lifecycle, include_retired, collector });
	}

	/**
	 * The bounded export link of one ranking.
	 *
	 * @param kind - `utilization` or `errors`.
	 * @param format - `csv` or `json`.
	 * @returns The API path.
	 */
	function exportHref(kind: string, format: string): string {
		return `/api/v1/exports/rankings/${kind}${qs({ ...request, format })}`;
	}
</script>

<svelte:head><title>Rankings · vakr</title></svelte:head>

<h1>Rankings</h1>
<div class="row">
	<div class="row" role="group" aria-label="Window">
		{#each WINDOWS as w (w)}
			<button type="button" aria-pressed={w === window} onclick={() => apply({ window: w })}
				>{w}</button
			>
		{/each}
	</div>
	<label
		>Rows
		<select value={String(rows)} onchange={(e) => apply({ rows: Number(e.currentTarget.value) })}>
			{#each ROWS as n (n)}<option value={String(n)}>{n}</option>{/each}
		</select></label
	>
</div>
{#if window === '24h'}
	<p class="muted">
		A 24-hour ranking reads every sample of the day; the first answer takes a few seconds.
	</p>
{/if}
<InterfaceFilters {filters} options={options.value} onChange={applyFilters} ranking />

<div class="grid wide">
	<section class="card" aria-label="Top utilization">
		<PageStatus
			state={pageState(utilization, { partial: (d) => completenessNotes(d, 'ranking') })}
			label="Ranking"
		>
			{#snippet children(view)}<RankingTables kind="utilization" {view} limit={rows} />{/snippet}
		</PageStatus>
		<p class="row exports">
			Download: <a href={exportHref('utilization', 'csv')} download>CSV</a>
			<a href={exportHref('utilization', 'json')} download>JSON</a>
		</p>
	</section>
	<section class="card" aria-label="Top errors">
		<PageStatus
			state={pageState(errors, { partial: (d) => completenessNotes(d, 'ranking') })}
			label="Ranking"
		>
			{#snippet children(view)}<RankingTables kind="errors" {view} limit={rows} />{/snippet}
		</PageStatus>
		<p class="row exports">
			Download: <a href={exportHref('errors', 'csv')} download>CSV</a>
			<a href={exportHref('errors', 'json')} download>JSON</a>
		</p>
	</section>
</div>

<style>
	.wide {
		grid-template-columns: repeat(auto-fill, minmax(34rem, 1fr));
	}
	.exports {
		font-size: 0.85rem;
	}
</style>
