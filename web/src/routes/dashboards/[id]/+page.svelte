<!--
@component
A custom dashboard (UI-004, UI-013, UI-014; DASH-T06).

One `/view` request per refresh, at the dashboard's own interval, resolves
every widget with current values; graph cards load as they scroll into
view. The page says how fresh the answer is, keeps it on screen marked
stale when a refresh fails, and names the position the values come from.
Admins get an Edit link; everyone gets the kiosk address of this dashboard.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import DashboardWidgets from '#lib/dashboards/DashboardWidgets.svelte';
	import { dashboardViewQuery } from '#lib/query/reads-dashboards.ts';
	import { fetchedAt, pageState } from '#lib/state/page-state.ts';

	const id = $derived(page.params['id'] ?? '');
	const query = createQuery(() => dashboardViewQuery(id));
	const shown = $derived(
		pageState(query, {
			partial: (d) => [
				...(d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]),
				...(d.truncated ? ['The inventory is larger than one read covers (E.29).'] : [])
			]
		})
	);
</script>

<svelte:head><title>{query.data?.name ?? 'Dashboard'} · vakr</title></svelte:head>

<p><a href="/dashboards">← Dashboards</a></p>
<PageStatus state={shown} label="Dashboard">
	{#snippet children(d)}
		<header class="heading">
			<h1>{d.name}</h1>
			{#if isAdmin()}<a class="button" href="/dashboards/{d.dashboard_id}/edit">Edit</a>{/if}
		</header>
		{#if d.description}<p>{d.description}</p>{/if}
		<p class="muted" data-testid="dashboard-freshness">
			Data as of {fetchedAt(query.dataUpdatedAt)} from {d.position || 'no collector'} · refreshes every
			{d.refresh_s} s · on a wall display:
			<code>/kiosk/dashboard/{d.dashboard_id}</code>
		</p>
		{#if d.widgets.length === 0}
			<p>This dashboard has no widgets yet{isAdmin() ? '; edit it to add some' : ''}.</p>
		{/if}
		<DashboardWidgets view={d} />
	{/snippet}
</PageStatus>

<style>
	.heading {
		display: flex;
		align-items: baseline;
		gap: var(--gap);
	}
</style>
