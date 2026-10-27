<!--
@component
Bulk select-and-tag on the interface list (META-013; admins only).

The admin ticks interfaces in the table (or every one on the page), names
vakr tags to add and to remove, and applies them in one change of at most
500 interfaces, all or nothing. Description tags are never touched. When
`inventory.toml` governs any selected interface, nothing changes and the
refusal names the file and the interfaces. Combined with the "Only not yet
classified" filter, this works through the interfaces whose descriptions
carry no keyword. The request carries the CSRF token and is never retried.

Example: `<BulkTag rows={page.rows} {selected} onSelectPage={all} onClear={clear} />`
-->
<script lang="ts">
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import type { InterfaceListRow, TagChangeOut } from '#lib/api/types.ts';
	import { names, refusalOf } from './edit-forms.ts';

	/** Interfaces one change may name (E.37). */
	const MAX = 500;

	interface Props {
		/** The rows on this page. */
		rows: InterfaceListRow[];
		/** Selected interface UUIDs, from any page. */
		selected: string[];
		/** Select or clear every row on this page. */
		onSelectPage: (on: boolean) => void;
		/** Clear the selection. */
		onClear: () => void;
		/** Interface UUID to "device interface", for the rows seen so far. */
		labels: Record<string, string>;
	}

	const { rows, selected, onSelectPage, onClear, labels }: Props = $props();
	const client = useQueryClient();
	let add = $state('');
	let remove = $state('');
	let outcome = $state<string | null>(null);
	let refused = $state<{ summary: string; which: string[] } | null>(null);
	const allOnPage = $derived(
		rows.length > 0 && rows.every((r) => selected.includes(r.ref.interface_id))
	);
	const ready = $derived(
		selected.length > 0 &&
			selected.length <= MAX &&
			(names(add).length > 0 || names(remove).length > 0)
	);

	const apply = createMutation(() => ({
		mutationFn: () =>
			unwrap(
				api.POST('/api/v1/interfaces/tags', {
					body: { interfaces: selected, add: names(add), remove: names(remove) }
				})
			),
		onSuccess: (answer: TagChangeOut) => {
			refused = null;
			outcome = `Tagged ${answer.changed.length} interface(s); ${answer.unchanged.length} already had these tags.`;
			announce(outcome);
			for (const key of ['interfaces', 'device-interfaces', 'annotation', 'tags']) {
				void client.invalidateQueries({ queryKey: [key] });
			}
			onClear();
		},
		onError: (error: unknown) => {
			const r = refusalOf(error, 'selection');
			const which = Object.keys(r.fields)
				.map((key) => /^interfaces\[(\d+)\]$/.exec(key)?.[1])
				.filter((n): n is string => n !== undefined)
				.map((n) => labels[selected[Number(n)] ?? ''] ?? `interface ${Number(n) + 1}`);
			refused = { summary: r.summary, which };
			outcome = null;
			announce(r.summary);
		}
	}));
</script>

<section class="card bulk" aria-labelledby="bulk-h">
	<h2 id="bulk-h">Tag selected interfaces</h2>
	<form
		class="row"
		onsubmit={(e) => {
			e.preventDefault();
			if (ready) apply.mutate();
		}}
	>
		<label
			><input
				type="checkbox"
				checked={allOnPage}
				disabled={rows.length === 0}
				onchange={(e) => onSelectPage(e.currentTarget.checked)}
			/>
			Select all {rows.length} on this page</label
		>
		<label
			>Tags to add <input bind:value={add} maxlength="4096" aria-describedby="bulk-note" /></label
		>
		<label
			>Tags to remove <input
				bind:value={remove}
				maxlength="4096"
				aria-describedby="bulk-note"
			/></label
		>
		<button type="submit" disabled={!ready || apply.isPending}>Apply to {selected.length}</button>
		<button type="button" onclick={onClear} disabled={selected.length === 0}>Clear selection</button
		>
	</form>
	<p id="bulk-note" class="muted note">
		Comma-separated vakr tags. Tags from descriptions are not changed. At most {MAX} interfaces per change.
	</p>
	<p role="status" data-testid="bulk-status">
		{selected.length} selected.{#if outcome}
			{outcome}{/if}
	</p>
	{#if refused}
		<div class="notice notice-red" role="alert">
			<strong>Not tagged.</strong>
			{refused.summary}
			{#if refused.which.length}<p>Managed by a file: {refused.which.join(', ')}.</p>{/if}
		</div>
	{/if}
</section>

<style>
	.note {
		font-size: 0.85rem;
		margin: 0.2rem 0;
	}
	label {
		display: inline-flex;
		gap: 0.3rem;
		align-items: center;
	}
</style>
