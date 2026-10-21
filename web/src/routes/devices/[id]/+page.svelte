<!--
@component
The device page (12.4, UI-003, UI-016, UI-017; WEB-T02, WEB-T03, WEB-T11).

Header: name and state (colour, icon, words), poll address, site, tags,
collector and latest poll age, admin state, each position's own verdict.
Summary: every fact the API gives (ICMP, latency and jitter, SNMP, agent
uptime with its disclaimer, intervals, discovery, quality), warnings, and
notes. Then the reachability graph, the interfaces (table or graph cards),
and the device's open identity reviews. Facts from stale evidence say so.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { deviceQuery } from '#lib/query/reads.ts';
	import { reviewsQuery } from '#lib/query/reads-catalog.ts';
	import { reachabilityQuery } from '#lib/query/reads-history.ts';
	import { readRange, rangeParams, type GraphRange } from '#lib/state/range.ts';
	import { healthNotes, pageState } from '#lib/state/page-state.ts';
	import { devicesHref, query as qs } from '#lib/links.ts';
	import DeviceInterfaces from '#lib/components/DeviceInterfaces.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import RangePicker from '#lib/components/RangePicker.svelte';
	import ReachabilitySection from '#lib/components/ReachabilitySection.svelte';
	import ReadingText from '#lib/components/ReadingText.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	const id = $derived(page.params['id'] ?? '');
	const range = $derived(readRange(page.url.searchParams));
	const params = $derived(rangeParams(range));
	const view = $derived(page.url.searchParams.get('view') === 'graphs' ? 'graphs' : 'table');
	const query = createQuery(() => deviceQuery(id));
	const reviews = createQuery(() => reviewsQuery(id, null));
	// The same request as the reachability section's (TanStack shares it): it names the zone.
	const reach = createQuery(() => reachabilityQuery(id, params));
	const zone = $derived(reach.data?.window.timezone ?? 'UTC');
	const shown = $derived(
		pageState(query, {
			partial: (d) => [
				...healthNotes(d.health),
				...(d.interfaces_truncated ? ['Only part of the interfaces is listed.'] : [])
			],
			stale: (d) =>
				d.row.freshness.value === 'stale'
					? 'The newest evidence for this device is older than the freshness threshold; values are not current.'
					: null
		})
	);

	/**
	 * Change the range or the interface view, keeping the rest of the URL.
	 *
	 * @param next - The new range, or `null` to keep it.
	 * @param nextView - The interface view.
	 */
	function navigate(next: GraphRange | null, nextView: string): void {
		const r = rangeParams(next ?? range);
		const q = qs({ ...r, view: nextView === 'graphs' ? 'graphs' : null });
		void goto(`/devices/${id}${q}`, { replace: true, reset: false });
	}
</script>

<svelte:head><title>{query.data?.row.name ?? 'Device'} · vakr</title></svelte:head>

<PageStatus state={shown} label="Device">
	{#snippet children(d)}
		<header class="device-header">
			<h1>{d.row.name}</h1>
			<StatusBadge color={d.row.status.color} icon={d.row.status.icon} label={d.row.status.label} />
			<dl class="facts header-facts">
				<dt>Poll address</dt>
				<dd class="mono">{d.row.address}</dd>
				<dt>Site</dt>
				<dd><ReadingText reading={d.row.site} /></dd>
				<dt>Tags</dt>
				<dd>
					{#each d.row.tags as t (t)}<a class="tag" href={devicesHref({ search: t })}>{t}</a
						>{:else}—{/each}
				</dd>
				<dt>Admin state</dt>
				<dd>{d.row.admin_state}</dd>
				<dt>Collector and last poll</dt>
				<dd>
					<ReadingText reading={d.row.collector} />, <ReadingText reading={d.row.last_poll} />
				</dd>
				<dt>Positions</dt>
				<dd>
					{#each d.row.positions as p (p.collector_id)}
						<span class="position">
							{p.position}:
							<StatusBadge
								small
								color={p.status.color}
								icon={p.status.icon}
								label={p.status.label}
							/>
							(<ReadingText reading={p.age} compact />)
						</span>
					{/each}
				</dd>
			</dl>
		</header>

		{#each d.warnings as w (w)}<p class="notice notice-amber" role="note">{w}</p>{/each}

		<section class="card" aria-labelledby="summary-h">
			<h2 id="summary-h">Summary</h2>
			<dl class="facts" data-testid="device-facts">
				{#each d.facts as fact, i (i)}
					<dt>{String(fact[0])}</dt>
					<dd><ReadingText reading={fact[1] as never} /></dd>
				{/each}
			</dl>
			{#each d.notes as n (n)}<p class="muted note">{n}</p>{/each}
		</section>

		<section aria-labelledby="reach-h">
			<h2 id="reach-h">Reachability</h2>
			<RangePicker {range} {zone} onChange={(r) => navigate(r, view)} />
			<ReachabilitySection deviceId={id} {params} />
		</section>

		<section aria-labelledby="ifaces-h">
			<h2 id="ifaces-h">Interfaces ({d.interfaces_total})</h2>
			<div class="row" role="group" aria-label="Interface view">
				<button
					type="button"
					aria-pressed={view === 'table'}
					onclick={() => navigate(null, 'table')}>Table</button
				>
				<button
					type="button"
					aria-pressed={view === 'graphs'}
					onclick={() => navigate(null, 'graphs')}>Graphs</button
				>
			</div>
			<DeviceInterfaces deviceId={id} {view} range={params} />
		</section>

		{#if reviews.data && reviews.data.items.length > 0}
			<section class="card" aria-labelledby="reviews-h">
				<h2 id="reviews-h">Identity reviews</h2>
				<p>
					{reviews.data.items.length} interface identity decision(s) wait for an admin.
					<a href="/identity-reviews?device={id}">Open the reviews</a>
				</p>
			</section>
		{/if}
	{/snippet}
</PageStatus>

<style>
	.device-header {
		display: grid;
		justify-items: start;
		gap: 0.4rem;
		margin-bottom: 0.6rem;
	}
	.device-header h1 {
		margin: 0;
	}
	.header-facts {
		font-size: 0.92rem;
	}
	.position,
	.tag {
		margin-right: 0.8rem;
	}
	.note {
		font-size: 0.85rem;
	}
</style>
