<!--
@component
"Pin to dashboard" on the interface page: add this interface to a custom dashboard (UI-004;
admins only).

The admin picks a dashboard; the control reads its current definition,
adds the interface to the first pinned widget with room (or a new pinned
widget), and saves with the version it read, so a concurrent edit is a
conflict to retry rather than a lost change (DB-003). Writes send the CSRF
token and are never retried (ADR-035 #6). The API checks the role again.

Example: `<PinToDashboard interfaceId={id} />`
-->
<script lang="ts">
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import { ApiFailure } from '#lib/api/errors.ts';
	import { dashboardsQuery } from '#lib/query/reads-dashboards.ts';
	import { pinInto } from './editor.ts';

	interface Props {
		interfaceId: string;
	}

	const { interfaceId }: Props = $props();
	const client = useQueryClient();
	const boards = createQuery(() => dashboardsQuery());
	let chosen = $state('');
	let outcome = $state<string | null>(null);

	/**
	 * Report an outcome on the page and to screen readers.
	 *
	 * @param text - One sentence.
	 */
	function tell(text: string): void {
		outcome = text;
		announce(text);
	}

	/**
	 * Pin the interface to the chosen dashboard.
	 *
	 * @returns The dashboard's name.
	 * @throws Error when the dashboard has no room or already holds it; ApiFailure when refused.
	 */
	async function pin(): Promise<string> {
		const path = { params: { path: { dashboard_uuid: chosen } } };
		const definition = await unwrap(api.GET('/api/v1/dashboards/{dashboard_uuid}', path));
		const change = pinInto(definition, interfaceId);
		if ('error' in change) throw new Error(change.error);
		await unwrap(api.PATCH('/api/v1/dashboards/{dashboard_uuid}', { ...path, body: change.body }));
		return definition.name;
	}

	const save = createMutation(() => ({
		mutationFn: pin,
		onSuccess: (name: string) => {
			tell(`Pinned to ${name}.`);
			void client.invalidateQueries({ queryKey: ['dashboard-view', chosen] });
			void client.invalidateQueries({ queryKey: ['dashboards'] });
		},
		onError: (error: unknown) => {
			if (error instanceof ApiFailure && error.kind === 'conflict') {
				tell('Someone changed that dashboard at the same moment; pin it again.');
			} else if (error instanceof ApiFailure) {
				tell(`Refused: ${error.message}.`);
			} else tell(error instanceof Error ? error.message : 'Pinning failed.');
		}
	}));
</script>

<section aria-labelledby="pin-h" class="card pin">
	<h2 id="pin-h">Pin to dashboard</h2>
	{#if boards.data && boards.data.rows.length > 0}
		<form
			class="row"
			onsubmit={(e) => {
				e.preventDefault();
				save.mutate();
			}}
		>
			<label for="pin-board">Dashboard</label>
			<select id="pin-board" bind:value={chosen} required>
				<option value="" disabled>Choose a dashboard</option>
				{#each boards.data.rows as b (b.id)}<option value={b.id}>{b.name}</option>{/each}
			</select>
			<button type="submit" disabled={chosen === '' || save.isPending}>Pin</button>
		</form>
	{:else if boards.data}
		<p>No custom dashboards yet. <a href="/dashboards/new">Create one</a> to pin interfaces.</p>
	{/if}
	{#if outcome}<p role="status" data-testid="pin-outcome">{outcome}</p>{/if}
</section>

<style>
	.pin {
		margin: var(--gap) 0;
	}
</style>
