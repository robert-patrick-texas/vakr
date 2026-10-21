<!--
@component
The device list (12.3, UI-005, UI-009, WEB-T01).

Default order is the 7.7 order: Attention, then Active, then
Offline/Retired. Filters, sort, page size, and density live in the URL; a
change starts again at the first page. Browsing by site, tag, group,
provider, or admin state is the same list with that filter (UI-009), named
at the top. The list refreshes every 30 s in place.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { devicesQuery, type DeviceQuery } from '#lib/query/reads.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import { readDeviceFilters, toQuery } from '#lib/state/filters.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { healthNotes, completenessNotes, pageState } from '#lib/state/page-state.ts';
	import DeviceFilters from '#lib/components/DeviceFilters.svelte';
	import DeviceTable from '#lib/components/DeviceTable.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';

	const options = filterOptions();
	const trail = new CursorTrail();
	const filters = $derived(readDeviceFilters(page.url.searchParams));
	const compact = $derived(page.url.searchParams.get('density') === 'compact');
	const key = $derived(toQuery(filters, 'status'));
	let lastKey = '';

	$effect(() => {
		// A different filter set starts at the first page.
		if (key !== lastKey) {
			lastKey = key;
			trail.reset();
		}
	});

	const query = createQuery(() => devicesQuery({ ...filters, cursor: trail.cursor }));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.rows.length === 0,
			partial: (d) => [...healthNotes(d.health), ...completenessNotes(d, 'device list')]
		})
	);
	const browsing = $derived(
		[
			['site', options.value.sites],
			['tag', options.value.tags],
			['group', options.value.groups],
			['collector', options.value.collectors]
		]
			.map(([name, list]) => {
				const id = filters[name as 'site'];
				const found = (list as { id: string; name: string }[]).find((x) => x.id === id);
				return id ? `${name} ${found?.name ?? id}` : null;
			})
			.concat(filters.provider ? [`provider ${filters.provider}`] : [])
			.concat(filters.admin_state ? [`admin state ${filters.admin_state}`] : [])
			.filter((x): x is string => x !== null)
	);

	/**
	 * Write new filters to the URL, keeping focus and scroll.
	 *
	 * @param next - The filters.
	 * @param dense - Compact rows.
	 */
	function apply(next: DeviceQuery, dense: boolean): void {
		const q = toQuery({ ...next, density: dense ? 'compact' : null }, 'status');
		void goto(`/devices${q ? `?${q}` : ''}`, {
			reset: false,
			replace: true
		});
	}
</script>

<svelte:head><title>Devices · vakr</title></svelte:head>

<h1>Devices</h1>
{#if browsing.length > 0}
	<p class="notice notice-gray" data-testid="browsing">
		Browsing by {browsing.join(', ')}. <a href="/devices">Show all devices</a>
	</p>
{/if}
<DeviceFilters {filters} options={options.value} {compact} onChange={apply} />
<PageStatus state={shown} label="Devices" emptyText="No device matches these filters.">
	{#snippet children(d)}
		<p class="row counts" aria-label="Matching devices by bucket">
			<span>Attention: <strong>{d.counts.attention}</strong></span>
			<span>Active: <strong>{d.counts.active}</strong></span>
			<span>Offline/Retired: <strong>{d.counts.offline}</strong></span>
		</p>
		<DeviceTable
			rows={d.rows}
			sort={filters.sort ?? 'status'}
			{compact}
			onSort={(sort) => apply({ ...filters, sort: sort as DeviceQuery['sort'] }, compact)}
		/>
		<Pager
			page={d.page}
			shown={d.rows.length}
			pageNumber={trail.number}
			noun="devices"
			onNext={trail.next}
			onPrevious={trail.previous}
			onFirst={trail.reset}
		/>
	{/snippet}
</PageStatus>

<style>
	.counts {
		margin: 0.3rem 0;
	}
</style>
