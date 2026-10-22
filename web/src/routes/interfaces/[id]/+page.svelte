<!--
@component
The interface page (12.5, UI-002; WEB-T03, WEB-T04, WEB-T06).

Identity and circuit metadata, current values, then traffic, utilization,
packets, and errors over the chosen range (presets 1h to 400d or a bounded
custom range), the admin/oper state timeline, and the identity history
with prior `ifIndex` values. Every graph names its resolution and zone.
A link back keeps the list's filters when the user came from a list.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import StatusTimeline from '#lib/charts/StatusTimeline.svelte';
	import PinToDashboard from '#lib/dashboards/PinToDashboard.svelte';
	import { deviceHref, query as qs } from '#lib/links.ts';
	import { interfaceQuery } from '#lib/query/reads.ts';
	import { interfaceGraphQuery, timelineQuery } from '#lib/query/reads-history.ts';
	import { readRange, rangeParams, type GraphRange } from '#lib/state/range.ts';
	import { completenessNotes, pageState } from '#lib/state/page-state.ts';
	import GraphPanel from '#lib/components/GraphPanel.svelte';
	import IdentityHistory from '#lib/components/IdentityHistory.svelte';
	import InterfaceFacts from '#lib/components/InterfaceFacts.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import RangePicker from '#lib/components/RangePicker.svelte';

	const id = $derived(page.params['id'] ?? '');
	const from = $derived(page.url.searchParams.get('from'));
	const back = $derived(
		from && /^\/(interfaces|devices|wan|high-priority|rankings)(\?|$)/.test(from) ? from : null
	);
	const range = $derived(readRange(page.url.searchParams));
	const params = $derived(rangeParams(range));
	const query = createQuery(() => interfaceQuery(id));
	// The same request as the traffic panel's (TanStack shares it): it names the display zone.
	const traffic = createQuery(() => interfaceGraphQuery(id, 'traffic', params));
	const zone = $derived(traffic.data?.window.timezone ?? 'UTC');
	const timeline = createQuery(() => timelineQuery(id, params));
	const shown = $derived(pageState(query));
	const timelineShown = $derived(
		pageState(timeline, { partial: (d) => completenessNotes(d, 'state timeline') })
	);
	const GRAPHS = [
		['traffic', 'Traffic'],
		['utilization', 'Utilization'],
		['packets', 'Packets'],
		['errors', 'Errors']
	] as const;

	/**
	 * Change the graph range, keeping the return link.
	 *
	 * @param next - The new range.
	 */
	function setRange(next: GraphRange): void {
		void goto(`/interfaces/${id}${qs({ ...rangeParams(next), from })}`, {
			replace: true,
			reset: false
		});
	}
</script>

<svelte:head><title>{query.data?.interface.if_name ?? 'Interface'} · vakr</title></svelte:head>

{#if back}<p><a href={back}>← Back to the list</a></p>{/if}
<PageStatus state={shown} label="Interface">
	{#snippet children(d)}
		<h1>
			<a href={deviceHref(d.interface.device_id)}>{d.current?.ref.device ?? 'Device'}</a> ›
			{d.interface.if_name || d.interface.if_descr}
		</h1>
		{#if d.interface.if_alias}<p class="muted">{d.interface.if_alias}</p>{/if}
		<InterfaceFacts detail={d} />
		{#if isAdmin()}<PinToDashboard interfaceId={id} />{/if}

		<section aria-labelledby="graphs-h">
			<h2 id="graphs-h">History</h2>
			<RangePicker {range} {zone} onChange={setRange} />
			{#each GRAPHS as [kind, title] (kind)}
				<GraphPanel interfaceId={id} {kind} {params} {title} />
			{/each}
		</section>

		<section aria-labelledby="timeline-h">
			<h2 id="timeline-h">State timeline</h2>
			<PageStatus state={timelineShown} label="State timeline">
				{#snippet children(t)}
					{#if t.available}
						<StatusTimeline view={t} />
						{#each t.notes as n (n)}<p class="muted note">{n}</p>{/each}
					{/if}
				{/snippet}
			</PageStatus>
		</section>

		<section aria-labelledby="identity-h">
			<h2 id="identity-h">Identity history</h2>
			<IdentityHistory interfaceId={id} {zone} />
			<p class="muted note">
				Identity reviews and the admin's decision are on the <a href="/identity-reviews"
					>identity reviews</a
				> page. Policy explanations and metadata editing are not in API v1 yet.
			</p>
		</section>
	{/snippet}
</PageStatus>

<style>
	.note {
		font-size: 0.85rem;
	}
</style>
