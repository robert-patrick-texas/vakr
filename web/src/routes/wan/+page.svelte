<!--
@component
WAN circuits with provider, circuit ID, contracted rate, and current use (DASH-002, UI-008).

Circuits are in name order, paged by the API (500 per page by default),
filterable by site, tag, group, and provider, and refreshed every 30 s.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { query as qs } from '#lib/links.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import { wanQuery } from '#lib/query/reads.ts';
	import { readInterfaceFilters } from '#lib/state/filters.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import InterfaceFilters from '#lib/components/InterfaceFilters.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import WanTable from '#lib/components/WanTable.svelte';

	const options = filterOptions();
	const trail = new CursorTrail();
	const filters = $derived(readInterfaceFilters(page.url.searchParams));
	const query = createQuery(() =>
		wanQuery({
			site: filters.site,
			tag: filters.tag,
			group: filters.group,
			provider: filters.provider,
			interest: filters.interest,
			lifecycle: filters.lifecycle,
			include_retired: filters.include_retired,
			collector: filters.collector,
			cursor: trail.cursor
		})
	);
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.rows.length === 0,
			partial: (d) =>
				d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]
		})
	);
</script>

<svelte:head><title>WAN circuits · vakr</title></svelte:head>

<h1>WAN circuits</h1>
<InterfaceFilters
	{filters}
	options={options.value}
	ranking
	onChange={(f) => {
		trail.reset();
		const { site, tag, group, provider, interest, lifecycle, include_retired, collector } = f;
		const q = qs({ site, tag, group, provider, interest, lifecycle, include_retired, collector });
		void goto(`/wan${q}`, {
			replace: true,
			reset: false
		});
	}}
/>
<PageStatus state={shown} label="WAN circuits" emptyText="No interface is marked as a WAN circuit.">
	{#snippet children(d)}
		<WanTable rows={d.rows} position={d.position} />
		<Pager
			page={d.page}
			shown={d.rows.length}
			pageNumber={trail.number}
			noun="circuits"
			onNext={trail.next}
			onPrevious={trail.previous}
			onFirst={trail.reset}
		/>
	{/snippet}
</PageStatus>
