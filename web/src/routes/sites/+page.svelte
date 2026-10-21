<!--
@component
Browse devices by site, tag, group, provider, and admin state (12.1 "Sites", UI-009).

Each entry opens the device list (or, for providers, the interface list)
filtered to it. Sites show their code, region, and time zone.
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { devicesHref, interfacesHref } from '#lib/links.ts';
	import { labelsQuery, sitesQuery } from '#lib/query/reads-catalog.ts';
	import { overviewQuery } from '#lib/query/reads.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';

	const sites = createQuery(() => sitesQuery());
	const tags = createQuery(() => labelsQuery('tags'));
	const groups = createQuery(() => labelsQuery('groups'));
	const overview = createQuery(() => ({
		...overviewQuery('15m'),
		refetchInterval: false as const
	}));
	const isEmpty = (d: { items: unknown[] }) => d.items.length === 0;
</script>

<svelte:head><title>Sites · vakr</title></svelte:head>

<h1>Sites, tags, groups, and providers</h1>

<section aria-labelledby="sites-h">
	<h2 id="sites-h">Sites</h2>
	<PageStatus state={pageState(sites, { isEmpty })} label="Sites" emptyText="No sites are defined.">
		{#snippet children(d)}
			<div class="table-wrap">
				<table class="density-compact">
					<caption class="sr-only">Sites</caption>
					<thead>
						<tr>
							<th scope="col">Site</th>
							<th scope="col">Code</th>
							<th scope="col">Region</th>
							<th scope="col">Time zone</th>
							<th scope="col">Browse</th>
						</tr>
					</thead>
					<tbody>
						{#each d.items as s (s.id)}
							<tr>
								<th scope="row">{s.name}</th>
								<td>{s.code}</td>
								<td>{s.region ?? '—'}</td>
								<td>{s.timezone ?? '—'}</td>
								<td>
									<a href={devicesHref({ site: s.id })}>Devices</a> ·
									<a href={interfacesHref({ site: s.id })}>Interfaces</a>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/snippet}
	</PageStatus>
</section>

<div class="grid">
	{#each [['Tags', 'tag', tags], ['Groups', 'group', groups]] as const as [title, key, q] (key)}
		<section class="card" aria-label={title}>
			<h2>{title}</h2>
			<PageStatus state={pageState(q, { isEmpty })} label={title} emptyText="None defined.">
				{#snippet children(d)}
					<ul>
						{#each d.items as item (item.id)}
							<li>
								<a href={devicesHref({ [key]: item.id })}>{item.name}</a>
								{#if item.description}<span class="muted">— {item.description}</span>{/if}
							</li>
						{/each}
					</ul>
				{/snippet}
			</PageStatus>
		</section>
	{/each}
	<section class="card" aria-labelledby="prov-h">
		<h2 id="prov-h">Providers</h2>
		<PageStatus state={pageState(overview)} label="Providers">
			{#snippet children(o)}
				<ul>
					{#each o.filters.providers as p (p)}
						<li><a href={interfacesHref({ provider: p })}>{p}</a></li>
					{:else}
						<li class="muted">No provider is named on any interface.</li>
					{/each}
				</ul>
			{/snippet}
		</PageStatus>
	</section>
	<section class="card" aria-labelledby="admin-h">
		<h2 id="admin-h">Admin state</h2>
		<ul>
			{#each ['enabled', 'maintenance', 'disabled', 'retired'] as s (s)}
				<li>
					<a href={devicesHref({ admin_state: s, include_retired: s === 'retired' })}>{s}</a>
				</li>
			{/each}
		</ul>
	</section>
</div>
