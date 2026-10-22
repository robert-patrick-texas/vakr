<!--
@component
The dashboards: the built-in ones and the custom ones admins define (12.1 "Dashboards", UI-004,
UI-008).

The built-in dashboards are the overview, rankings, high-priority
interfaces, and WAN circuits. Custom dashboards pin interfaces across
devices and show selections by site, tag, group, provider, or WAN role;
admins create and edit them here, everyone else views them. Public
dashboards (UI-007) come in a later release; the page says so.
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import { dashboardsQuery } from '#lib/query/reads-dashboards.ts';
	import { pageState } from '#lib/state/page-state.ts';

	const custom = createQuery(() => dashboardsQuery());
	const shown = $derived(pageState(custom, { isEmpty: (d) => d.rows.length === 0 }));
</script>

<svelte:head><title>Dashboards · vakr</title></svelte:head>

<h1>Dashboards</h1>
<div class="grid">
	<a class="card tile" href="/">
		<h2>Overview</h2>
		<p>Device state, health, high priority, WAN, top utilization and errors, events.</p>
	</a>
	<a class="card tile" href="/rankings">
		<h2>Rankings</h2>
		<p>Top utilization (p95) and top errors for 15 minutes to 24 hours, with filters.</p>
	</a>
	<a class="card tile" href="/high-priority">
		<h2>High-priority interfaces</h2>
		<p>Every primary-interest interface with current values.</p>
	</a>
	<a class="card tile" href="/wan">
		<h2>WAN circuits</h2>
		<p>Circuits with provider, circuit ID, contracted rate, and current use.</p>
	</a>
</div>

<section aria-labelledby="custom-h">
	<div class="heading">
		<h2 id="custom-h">Custom dashboards</h2>
		{#if isAdmin()}<a class="button" href="/dashboards/new">New dashboard</a>{/if}
	</div>
	<PageStatus
		state={shown}
		label="Custom dashboards"
		emptyText={isAdmin()
			? 'No custom dashboards yet. Create one to pin interfaces across devices.'
			: 'No custom dashboards yet. An admin can create them.'}
	>
		{#snippet children(d)}
			<ul class="custom">
				{#each d.rows as b (b.id)}
					<li>
						<a href="/dashboards/{b.id}">{b.name}</a>
						<span class="muted"
							>{b.widgets} widget{b.widgets === 1 ? '' : 's'} · refreshes every {b.refresh_s} s</span
						>
						{#if b.description}<div class="muted">{b.description}</div>{/if}
					</li>
				{/each}
			</ul>
		{/snippet}
	</PageStatus>
</section>
<p class="muted">Public dashboards for anonymous viewers are not in this release.</p>

<style>
	.tile {
		text-decoration: none;
		color: var(--fg);
	}
	.tile h2 {
		margin-top: 0;
		color: var(--link);
	}
	.heading {
		display: flex;
		align-items: baseline;
		gap: var(--gap);
	}
	.custom {
		list-style: none;
		padding: 0;
	}
	.custom li {
		margin-bottom: 0.6rem;
	}
	.custom a {
		font-size: 1.1rem;
		margin-right: 0.6rem;
	}
</style>
