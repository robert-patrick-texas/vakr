<!--
@component
The interface annotation form (META-007, META-011, META-013; admins only).

Interest, sticky, WAN circuit, provider, circuit ID, contracted rate,
capacity override, vakr tags, and comment, with the rules of an
`[[interface_annotations]]` entry. vakr tags are separate from the tags the
description gives the interface, which are shown but not edited here. An
interface `inventory.toml` governs (claimed) is shown read-only, each field
saying which file manages it. A change carries the version the form opened
(DB-003); a peer node's refusal names its primary (MPOS-003).

Example: `<AnnotationForm annotation={a} onSaved={keep} onReload={refetch} />`
-->
<script module lang="ts">
	const TEXTS = [
		['provider', 'Provider', 'Carrier or ISP.'],
		['circuit_id', 'Circuit ID', 'The carrier’s circuit ID.'],
		['contracted_bps', 'Contracted rate (bit/s)', 'Whole bits per second; blank for none.'],
		['capacity_override_bps', 'Capacity override (bit/s)', 'Used instead of ifSpeed.'],
		['tags', 'vakr tags', 'Comma-separated; kept apart from description tags (META-013).']
	] as const;
</script>

<script lang="ts">
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { tick } from 'svelte';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import type { AnnotationOut } from '#lib/api/types.ts';
	import {
		annotationDraft,
		rateProblem,
		refusalOf,
		toAnnotationPatch,
		type Refusal
	} from './edit-forms.ts';
	import FormField from './FormField.svelte';

	interface Props {
		annotation: AnnotationOut;
		onSaved: (answer: AnnotationOut) => void;
		onReload: () => void;
	}

	const { annotation, onSaved, onReload }: Props = $props();
	// The panel re-creates the form for each version (`{#key}`), so these are fixed.
	// svelte-ignore state_referenced_locally
	const a = annotation;
	const before = annotationDraft(a);
	const draft = $state({ ...before });
	let refusal = $state<Refusal | null>(null);
	let summary: HTMLElement | null = $state(null);
	const owner = a.editable ? null : a.managed_by_source;
	const client = useQueryClient();
	const local = $derived({
		contracted_bps: rateProblem(draft.contracted_bps),
		capacity_override_bps: rateProblem(draft.capacity_override_bps)
	});

	/**
	 * The problem or ownership note of a field.
	 *
	 * @param field - The field.
	 * @returns The message, or `null`.
	 */
	function problem(field: string): string | null {
		const own = (local as Record<string, string | null>)[field] ?? null;
		return own ?? refusal?.fields[field] ?? (owner ? `Managed by ${owner}.` : null);
	}

	const save = createMutation(() => ({
		mutationFn: () =>
			unwrap(
				api.PATCH('/api/v1/interfaces/{interface_uuid}/annotation', {
					params: { path: { interface_uuid: a.id } },
					body: toAnnotationPatch(draft, before, a.version)
				})
			),
		onSuccess: (answer: AnnotationOut) => {
			client.setQueryData(['annotation', answer.id], answer);
			for (const key of ['interface', 'interfaces', 'device-interfaces', 'high-priority', 'wan']) {
				void client.invalidateQueries({ queryKey: [key] });
			}
			announce(answer.changes.length ? 'Annotation saved.' : 'Nothing to change.');
			onSaved(answer);
		},
		onError: async (error: unknown) => {
			refusal = refusalOf(error, 'interface');
			announce(refusal.summary);
			await tick();
			summary?.focus();
		}
	}));
</script>

<form
	class="inventory-form"
	aria-label="Annotation"
	onsubmit={(e) => {
		e.preventDefault();
		if (!local.contracted_bps && !local.capacity_override_bps) save.mutate();
	}}
>
	{#if owner}
		<p class="notice notice-amber" data-testid="managed-by-file">
			Managed by {owner}: change its <code>[[interface_annotations]]</code> entry there and run
			<code>vakr inventory apply</code> (META-008).
		</p>
	{/if}
	<fieldset disabled={owner !== null}>
		<legend class="sr-only">Annotation fields</legend>
		<div class="grid">
			<FormField
				id="ann-interest"
				label="Interest"
				note="High priority is the primary class (META-011)."
				problem={problem('interest')}
			>
				<select id="ann-interest" aria-describedby="ann-interest-note" bind:value={draft.interest}>
					<option value="primary">High priority (primary)</option>
					<option value="secondary">Other (secondary)</option>
				</select>
			</FormField>
			{#each [['sticky', 'Sticky', 'Keep monitoring when discovery stops seeing it.'], ['wan_circuit', 'WAN circuit', 'Lists it with the WAN circuits.']] as const as [field, label, note] (field)}
				<FormField id="ann-{field}" {label} {note} problem={problem(field)}>
					<input
						id="ann-{field}"
						type="checkbox"
						aria-describedby="ann-{field}-note"
						bind:checked={draft[field]}
					/>
				</FormField>
			{/each}
			{#each TEXTS as [field, label, note] (field)}
				<FormField id="ann-{field}" {label} {note} problem={problem(field)}>
					<input
						id="ann-{field}"
						maxlength={field === 'tags' ? 4096 : 255}
						inputmode={field.endsWith('_bps') ? 'numeric' : undefined}
						autocomplete="off"
						aria-describedby="ann-{field}-note"
						aria-invalid={problem(field) && !owner ? 'true' : undefined}
						bind:value={draft[field]}
					/>
				</FormField>
			{/each}
			<FormField
				id="ann-comment"
				label="Comment"
				note="Up to 4,096 characters."
				problem={problem('comment')}
			>
				<textarea
					id="ann-comment"
					rows="2"
					maxlength="4096"
					aria-describedby="ann-comment-note"
					bind:value={draft.comment}></textarea>
			</FormField>
		</div>
	</fieldset>
	{#if refusal}
		<div class="notice notice-red" role="alert" tabindex="-1" bind:this={summary}>
			<strong>Not saved.</strong>
			{refusal.summary}
			{#if refusal.conflict}<button type="button" onclick={onReload}>Reload the annotation</button
				>{/if}
		</div>
	{/if}
	{#if !owner}<button type="submit" disabled={save.isPending}>Save annotation</button>{/if}
</form>

<style>
	.inventory-form fieldset {
		border: none;
		margin: 0;
		padding: 0;
	}
	.grid {
		display: grid;
		grid-template-columns: max-content minmax(12rem, 32rem);
		gap: 0.6rem 0.8rem;
		align-items: start;
		margin-bottom: var(--gap);
	}
</style>
