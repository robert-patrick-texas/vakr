<!--
@component
Global search results (12.1, UI-017).

Plain, case-insensitive substring search over devices (name, address,
UUID), interfaces (name, alias, description), sites, providers, circuits,
tags, and groups. Each hit links to its page, so any interface's history
is two interactions from the search box (UI-017).
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import type { SearchHit } from '#lib/api/types.ts';
	import { deviceHref, devicesHref, interfaceHref, interfacesHref } from '#lib/links.ts';
	import { searchQuery } from '#lib/query/reads-catalog.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';

	const q = $derived((page.url.searchParams.get('q') ?? '').slice(0, 256));
	// svelte-ignore state_referenced_locally
	let text = $state(q);
	const query = createQuery(() => searchQuery(q));
	const shown = $derived(pageState(query, { isEmpty: (d) => d.hits.length === 0 }));
	const KINDS: Record<string, string> = {
		device: 'Device',
		interface: 'Interface',
		site: 'Site',
		provider: 'Provider',
		circuit: 'Circuit',
		tag: 'Tag',
		group: 'Group'
	};

	/**
	 * Where a hit leads.
	 *
	 * @param hit - One search hit.
	 * @returns The page for it.
	 */
	function hrefOf(hit: SearchHit): string {
		if (hit.interface_id) return interfaceHref(hit.interface_id);
		if (hit.kind === 'device' && hit.device_id) return deviceHref(hit.device_id);
		if (hit.kind === 'site' && hit.id) return devicesHref({ site: hit.id });
		if (hit.kind === 'tag' && hit.id) return devicesHref({ tag: hit.id });
		if (hit.kind === 'group' && hit.id) return devicesHref({ group: hit.id });
		if (hit.kind === 'provider') return interfacesHref({ provider: hit.label });
		return devicesHref({ search: hit.label });
	}
</script>

<svelte:head><title>Search · vakr</title></svelte:head>

<h1>Search</h1>
<form
	role="search"
	class="row"
	onsubmit={(e) => {
		e.preventDefault();
		void goto(`/search?q=${encodeURIComponent(text.trim())}`, { replace: true, reset: false });
	}}
>
	<label>Find <input type="search" maxlength="256" bind:value={text} /></label>
	<button type="submit">Search</button>
</form>
{#if q}
	<PageStatus state={shown} label="Results" emptyText="Nothing matches “{q}”.">
		{#snippet children(d)}
			<p class="muted">
				{d.totals.map((t) => `${t[1]} ${t[0]}`).join(', ')}{d.truncated
					? ' (some kinds have more hits than shown)'
					: ''}.
			</p>
			<ul class="hits">
				{#each d.hits as hit, i (i)}
					<li>
						<span class="kind">{KINDS[hit.kind] ?? hit.kind}</span>
						<a href={hrefOf(hit)}>{hit.label}</a>
						<span class="muted">{hit.detail} · matched {hit.matched}</span>
					</li>
				{/each}
			</ul>
		{/snippet}
	</PageStatus>
{:else}
	<p class="muted">
		Type part of a device name or address, an interface name or description, a site, provider,
		circuit, tag, or group.
	</p>
{/if}

<style>
	.hits {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.3rem;
	}
	.kind {
		display: inline-block;
		min-width: 5.5rem;
		font-weight: 600;
	}
</style>
