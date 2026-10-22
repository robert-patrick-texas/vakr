<!--
@component
Edit a custom dashboard (UI-004, DB-003; admins only).

The editor opens on the stored definition and its version; the dashboard's
view supplies the names of the pinned interfaces. After a conflict, Reload
reads the newer version and opens the editor on it.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import type { DashboardView } from '#lib/api/types.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import DashboardEditor from '#lib/dashboards/DashboardEditor.svelte';
	import { fromDefinition } from '#lib/dashboards/editor.ts';
	import { dashboardQuery, dashboardViewQuery } from '#lib/query/reads-dashboards.ts';
	import { pageState } from '#lib/state/page-state.ts';

	const id = $derived(page.params['id'] ?? '');
	const definition = createQuery(() => dashboardQuery(id));
	const view = createQuery(() => dashboardViewQuery(id));
	const shown = $derived(pageState(definition));

	/**
	 * Name every pinned interface the view knows.
	 *
	 * @param v - The dashboard's view, if loaded.
	 * @returns Interface UUID to "device name".
	 */
	function namesOf(v: DashboardView | undefined): Record<string, string> {
		const out: Record<string, string> = {};
		for (const w of v?.widgets ?? []) {
			if (w.kind !== 'pinned_interfaces') continue;
			for (const e of w.entries) {
				const ref = e.row?.ref ?? e.ref;
				out[e.interface_id] = ref ? `${ref.device} ${ref.name}` : 'Interface (no longer exists)';
			}
		}
		return out;
	}
</script>

<svelte:head><title>Edit {definition.data?.name ?? 'dashboard'} · vakr</title></svelte:head>

<p><a href="/dashboards/{id}">← Back to the dashboard</a></p>
{#if !isAdmin()}
	<p class="notice notice-amber">Only an admin can change dashboards.</p>
{:else}
	<PageStatus state={shown} label="Dashboard">
		{#snippet children(d)}
			<h1>Edit {d.name}</h1>
			{#if view.isPending}
				<p class="muted">Loading the pinned interfaces’ names…</p>
			{:else}
				{#key d.version}
					<DashboardEditor
						initial={fromDefinition(d)}
						{id}
						version={d.version}
						names={namesOf(view.data)}
						onReload={() => void definition.refetch()}
					/>
				{/key}
			{/if}
		{/snippet}
	</PageStatus>
{/if}
