<!--
@component
The dashboard editor: name, description, refresh, and the widgets (UI-004; admins only).

The draft is the API's body shape. Saving creates the dashboard (POST) or
changes it based on the version the editor opened (PATCH, DB-003): if
someone else saved in between, the API answers 409 and the editor says so
and offers to reload, rather than overwriting their change. What the
editor can check itself (`problems`) it says before saving; everything
else, such as an interface that no longer exists, the API names by field.
Writes send the CSRF token and are never retried (ADR-035 #6).

Example: `<DashboardEditor initial={newDraft()} id={null} version={null} names={{}} />`
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap, unwrapEmpty } from '#lib/api/client.ts';
	import { ApiFailure } from '#lib/api/errors.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import {
		KIND_NAMES,
		LIMITS,
		REFRESH_CHOICES,
		emptyWidget,
		move,
		problems,
		toCreate,
		toPatch,
		type Draft,
		type WidgetKind
	} from './editor.ts';
	import WidgetEditor from './WidgetEditor.svelte';

	interface Props {
		initial: Draft;
		/** The dashboard being changed, or null for a new one. */
		id: string | null;
		/** The version the editor opened (DB-003), or null for a new one. */
		version: number | null;
		names: Record<string, string>;
		/** Reload the stored dashboard after a conflict. */
		onReload?: () => void;
	}

	const { initial, id, version, names, onReload }: Props = $props();
	// svelte-ignore state_referenced_locally
	let draft = $state<Draft>(structuredClone(initial));
	// svelte-ignore state_referenced_locally
	const known = $state<Record<string, string>>({ ...names });
	const options = filterOptions();
	const client = useQueryClient();
	const found = $derived(problems(draft));
	let adding = $state<WidgetKind>('pinned_interfaces');
	let outcome = $state<string | null>(null);
	let conflict = $state(false);
	let confirmDelete = $state(false);

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
	 * Explain a refused save.
	 *
	 * @param error - What the request threw.
	 */
	function refused(error: unknown): void {
		if (error instanceof ApiFailure && error.kind === 'conflict' && error.code === 'conflict') {
			// 409 is also a taken name; only a stale version says it was changed by someone else.
			conflict = /changed by someone else/.test(error.message);
			tell(conflict ? 'Someone changed this dashboard since you opened it. Reload to see their ' +
				'version, then apply your change again.' : `Refused: ${error.message}.`); // prettier-ignore
		} else if (error instanceof ApiFailure) {
			const where = error.fields.map((f) => f.field).join(', ');
			tell(`Refused: ${error.message}${where ? ` (${where})` : ''}.`);
		} else tell('Saving failed.');
	}

	const save = createMutation(() => ({
		mutationFn: async () => {
			if (id === null) return unwrap(api.POST('/api/v1/dashboards', { body: toCreate(draft) }));
			const path = { params: { path: { dashboard_uuid: id } } };
			const body = toPatch(draft, version ?? 1);
			return unwrap(api.PATCH('/api/v1/dashboards/{dashboard_uuid}', { ...path, body }));
		},
		onSuccess: (saved: { id: string; name: string }) => {
			void client.invalidateQueries({ queryKey: ['dashboards'] });
			void client.invalidateQueries({ queryKey: ['dashboard', saved.id] });
			void client.invalidateQueries({ queryKey: ['dashboard-view', saved.id] });
			announce(`Saved ${saved.name}.`);
			void goto(`/dashboards/${saved.id}`);
		},
		onError: refused
	}));

	const remove = createMutation(() => ({
		mutationFn: () =>
			unwrapEmpty(
				api.DELETE('/api/v1/dashboards/{dashboard_uuid}', {
					params: { path: { dashboard_uuid: id ?? '' } }
				})
			),
		onSuccess: () => {
			void client.invalidateQueries({ queryKey: ['dashboards'] });
			announce(`Deleted ${draft.name}.`);
			void goto('/dashboards');
		},
		onError: refused
	}));
</script>

<form
	class="editor"
	onsubmit={(e) => {
		e.preventDefault();
		if (found.length === 0) save.mutate();
	}}
>
	<div class="fields">
		<label for="d-name">Name</label>
		<input id="d-name" maxlength={LIMITS.name} required bind:value={draft.name} />
		<label for="d-description">Description</label>
		<textarea
			id="d-description"
			maxlength={LIMITS.description}
			rows="2"
			bind:value={draft.description}></textarea>
		<label for="d-refresh">Refresh every</label>
		<select id="d-refresh" bind:value={draft.refresh_s}>
			{#each REFRESH_CHOICES as s (s)}<option value={s}
					>{s < 60 ? `${s} s` : `${s / 60} min`}</option
				>{/each}
		</select>
	</div>

	<h2>Widgets</h2>
	{#each draft.widgets as widget, n (n)}
		<WidgetEditor
			bind:widget={() => widget, (next) => (draft.widgets[n] = next)}
			index={n}
			count={draft.widgets.length}
			options={options.value}
			names={known}
			onMove={(delta) => (draft.widgets = move(draft.widgets, n, delta))}
			onRemove={() => (draft.widgets = draft.widgets.filter((_, k) => k !== n))}
		/>
	{/each}
	<div class="row">
		<label for="d-add">Add a widget</label>
		<select id="d-add" bind:value={adding}>
			{#each Object.entries(KIND_NAMES) as [kind, name] (kind)}<option value={kind}>{name}</option
				>{/each}
		</select>
		<button
			type="button"
			disabled={draft.widgets.length >= LIMITS.widgets}
			onclick={() => (draft.widgets = [...draft.widgets, emptyWidget(adding)])}>Add</button
		>
	</div>

	{#if found.length > 0}
		<ul class="problems" aria-label="Before saving">
			{#each found as p (p)}<li>{p}</li>{/each}
		</ul>
	{/if}
	<div class="row">
		<button type="submit" disabled={found.length > 0 || save.isPending}>Save</button>
		<a href={id ? `/dashboards/${id}` : '/dashboards'}>Cancel</a>
		{#if id !== null}
			{#if confirmDelete}
				<button type="button" onclick={() => remove.mutate()}>Yes, delete {draft.name}</button>
				<button type="button" onclick={() => (confirmDelete = false)}>Keep it</button>
			{:else}
				<button type="button" onclick={() => (confirmDelete = true)}>Delete dashboard</button>
			{/if}
		{/if}
	</div>
	{#if outcome}<p role="status" data-testid="editor-outcome">{outcome}</p>{/if}
	{#if conflict && onReload}<button type="button" onclick={onReload}>Reload the dashboard</button
		>{/if}
</form>

<style>
	.fields {
		display: grid;
		grid-template-columns: max-content minmax(12rem, 32rem);
		gap: 0.5rem 0.8rem;
		align-items: center;
		margin-bottom: var(--gap);
	}
	.problems {
		color: var(--fg-muted);
	}
</style>
