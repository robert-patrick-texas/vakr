<!--
@component
The overview's device summary, recent events, and quick filters (12.2 items 1, 7, 8; UI-008).

Device counts link to the filtered device list; every state shows its icon
and words. Event feeds that vakr cannot produce yet say so instead of
looking empty. Quick filters open the device or interface list for one
provider, site, tag, or group, and the WAN view (UI-009). On a kiosk
display the links are plain text and the quick filters, which only
navigate, are left out (UI-013).

Example: `<OverviewPanels overview={data} />`
-->
<script lang="ts">
	import type { OverviewAnswer } from '#lib/query/reads.ts';
	import { formatInstant } from '#lib/format/time.ts';
	import { devicesHref, interfacesHref, deviceHref } from '#lib/links.ts';
	import { inKiosk } from '#lib/state/kiosk-context.ts';
	import Link from './Link.svelte';
	import StatusBadge from './StatusBadge.svelte';

	interface Props {
		overview: OverviewAnswer;
		zone: string;
	}

	const { overview, zone }: Props = $props();
	const d = $derived(overview.devices);
	const kiosk = inKiosk();
	const FEEDS: Record<string, string> = {
		restart: 'Recent restarts',
		rediscovery: 'Rediscovery',
		identity_review: 'Identity reviews',
		telemetry_loss: 'Telemetry loss'
	};
</script>

<section class="card" aria-labelledby="summary-h">
	<h2 id="summary-h">Device state</h2>
	<p class="row">
		<Link href={devicesHref({ bucket: ['attention'] })}
			>Attention: <strong>{d.counts.attention}</strong></Link
		>
		<Link href={devicesHref({ bucket: ['active'] })}
			>Active: <strong>{d.counts.active}</strong></Link
		>
		<Link href={devicesHref({ bucket: ['offline'] })}
			>Offline/Retired: <strong>{d.counts.offline}</strong></Link
		>
		<span class="muted">of {d.total}{d.truncated ? ' (truncated)' : ''}</span>
	</p>
	<ul class="plain">
		{#each d.by_status as s (s.label)}
			<li>
				<StatusBadge small color={s.color as 'red'} icon={s.icon} label={s.label} />
				<strong>{s.count}</strong>
			</li>
		{/each}
	</ul>
	<p class="muted">
		Evidence: <Link href={devicesHref({ freshness: ['fresh'] })}>{d.fresh} fresh</Link>,
		<Link href={devicesHref({ freshness: ['stale'] })}>{d.stale} stale</Link>,
		<Link href={devicesHref({ freshness: ['never'] })}>{d.never} never observed</Link>.
	</p>
</section>

<section class="card" aria-labelledby="events-h">
	<h2 id="events-h">Recent events</h2>
	{#each overview.events as feed (feed.kind)}
		<h3>{FEEDS[feed.kind] ?? feed.kind} ({feed.total})</h3>
		{#if !feed.available}
			<p class="muted">Not available: {feed.reason}</p>
		{:else if feed.rows.length === 0}
			<p class="muted">None{feed.reason ? `: ${feed.reason}` : ''}.</p>
		{:else}
			<ul class="plain small">
				{#each feed.rows as e, i (i)}
					<li>
						{formatInstant(e.at, zone)}
						{#if e.device_id}<Link href={deviceHref(e.device_id)}>{e.subject}</Link
							>{:else}{e.subject}{/if}:
						{e.text}
						{#if feed.kind === 'identity_review' && !kiosk}<a href="/identity-reviews">review</a
							>{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/each}
</section>

{#if !kiosk}
	<section class="card" aria-labelledby="filters-h">
		<h2 id="filters-h">Quick filters</h2>
		<p>
			<a href="/wan">WAN circuits ({overview.filters.wan_circuits})</a> ·
			<a href="/high-priority">High-priority interfaces</a>
		</p>
		{#if overview.filters.providers.length > 0}
			<h3>Providers</h3>
			<p class="row">
				{#each overview.filters.providers as p (p)}<a href={interfacesHref({ provider: p })}>{p}</a
					>{/each}
			</p>
		{/if}
		{#each [['Sites', 'site', overview.filters.sites], ['Tags', 'tag', overview.filters.tags], ['Groups', 'group', overview.filters.groups]] as const as [title, key, list] (key)}
			{#if list.length > 0}
				<h3>{title}</h3>
				<p class="row">
					{#each list as item (String(item[0]))}
						<a href={devicesHref({ [key]: String(item[0]) })}>{String(item[1])}</a>
					{/each}
				</p>
			{/if}
		{/each}
	</section>
{/if}

<style>
	.plain {
		list-style: none;
		padding: 0;
		margin: 0.3rem 0;
		display: grid;
		gap: 0.25rem;
	}
	.small {
		font-size: 0.85rem;
	}
</style>
