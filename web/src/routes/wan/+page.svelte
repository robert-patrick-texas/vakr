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
	import { bitRate } from '#lib/format/units.ts';
	import { deviceHref, interfaceHref, query as qs } from '#lib/links.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import { wanQuery } from '#lib/query/reads.ts';
	import { readInterfaceFilters } from '#lib/state/filters.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import InterfaceFilters from '#lib/components/InterfaceFilters.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import ReadingText from '#lib/components/ReadingText.svelte';

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
		<div class="table-wrap">
			<table class="density-compact">
				<caption>WAN circuits (current values from {d.position})</caption>
				<thead>
					<tr>
						<th scope="col">Circuit</th>
						<th scope="col">Provider</th>
						<th scope="col">Device and interface</th>
						<th scope="col">Site</th>
						<th scope="col" class="num">Contracted</th>
						<th scope="col" class="num">In / out</th>
						<th scope="col" class="num">Util. in / out</th>
						<th scope="col">Admin/oper</th>
						<th scope="col" class="num">Age</th>
					</tr>
				</thead>
				<tbody>
					{#each d.rows as r (r.ref.interface_id)}
						<tr>
							<th scope="row">{r.ref.circuit_id ?? '—'}</th>
							<td>{r.ref.provider ?? '—'}</td>
							<td>
								<a href={deviceHref(r.ref.device_id)}>{r.ref.device}</a>
								<a href={interfaceHref(r.ref.interface_id, { from: '/wan' })}>{r.ref.name}</a>
							</td>
							<td>{r.ref.site ?? '—'}</td>
							<td class="num">{bitRate(r.contracted_bps)}</td>
							<td class="num">
								<ReadingText reading={r.current.in_rate} compact /> /
								<ReadingText reading={r.current.out_rate} compact />
							</td>
							<td class="num">
								<ReadingText reading={r.current.in_util} compact /> /
								<ReadingText reading={r.current.out_util} compact />
							</td>
							<td><ReadingText reading={r.current.state} compact /></td>
							<td class="num"><ReadingText reading={r.current.age} compact /></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
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
