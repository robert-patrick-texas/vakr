<!--
@component
The global interface list (12.5, UI-002, UI-006; WEB-T03).

Sticky, WAN, and high-priority interfaces come first unless another sort is
chosen. Filters, sort, and page size live in the URL, and links to an
interface keep them, so Back returns to the same filtered list. Pages are
the API's (at most 500 rows, E.19); nothing loads every interface.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { interfacesQuery, type InterfaceQuery } from '#lib/query/reads.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import { readInterfaceFilters, toQuery } from '#lib/state/filters.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
	import InterfaceFilters from '#lib/components/InterfaceFilters.svelte';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';

	const options = filterOptions();
	const trail = new CursorTrail();
	const filters = $derived(readInterfaceFilters(page.url.searchParams));
	const key = $derived(toQuery(filters, 'priority'));
	let lastKey = '';

	$effect(() => {
		if (key !== lastKey) {
			lastKey = key;
			trail.reset();
		}
	});

	const query = createQuery(() => interfacesQuery({ ...filters, cursor: trail.cursor }));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.rows.length === 0,
			partial: (d) => [
				...(d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]),
				...completenessNotes(d, 'interface list')
			]
		})
	);

	/**
	 * Write new filters to the URL, keeping focus and scroll.
	 *
	 * @param next - The filters.
	 */
	function apply(next: InterfaceQuery): void {
		const q = toQuery(next, 'priority');
		void goto(`/interfaces${q ? `?${q}` : ''}`, { replace: true, reset: false });
	}
</script>

<svelte:head><title>Interfaces · vakr</title></svelte:head>

<h1>Interfaces</h1>
<InterfaceFilters {filters} options={options.value} onChange={apply} />
<PageStatus state={shown} label="Interfaces" emptyText="No interface matches these filters.">
	{#snippet children(d)}
		<p class="muted">Current values from position {d.position}.</p>
		<InterfaceTable
			rows={d.rows}
			showDevice
			sort={filters.sort ?? 'priority'}
			onSort={(sort) => apply({ ...filters, sort: sort as InterfaceQuery['sort'] })}
			keep={{ from: `/interfaces?${key}` }}
		/>
		<Pager
			page={d.page}
			shown={d.rows.length}
			pageNumber={trail.number}
			noun="interfaces"
			onNext={trail.next}
			onPrevious={trail.previous}
			onFirst={trail.reset}
		/>
	{/snippet}
</PageStatus>
