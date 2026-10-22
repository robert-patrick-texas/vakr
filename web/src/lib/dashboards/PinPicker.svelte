<!--
@component
The pinned interfaces of one widget in the dashboard editor: find, add, order, remove (UI-004).

Interfaces are found with the global search (bounded, 12.1) and added in
the order the admin wants them shown, at most `LIMITS.pinned` (E.31).
Pinned interfaces are named from `names` (filled from the dashboard's view
and from each search hit added), so the list never shows bare UUIDs.

Example: `<PinPicker bind:interfaces={w.interfaces} {names} idPrefix="w0" />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { searchQuery } from '#lib/query/reads-catalog.ts';
	import { LIMITS, move } from './editor.ts';

	interface Props {
		interfaces: string[];
		/** Interface UUID to "device name"; the picker adds what it finds. */
		names: Record<string, string>;
		idPrefix: string;
	}

	let { interfaces = $bindable(), names, idPrefix }: Props = $props();
	let typed = $state('');
	let term = $state('');
	const found = createQuery(() => searchQuery(term));
	const hits = $derived(
		(found.data?.hits ?? []).filter((h) => h.kind === 'interface' && h.interface_id)
	);

	/**
	 * Pin one interface at the end.
	 *
	 * @param id - Interface UUID.
	 * @param label - How to name it.
	 */
	function add(id: string, label: string): void {
		if (interfaces.includes(id) || interfaces.length >= LIMITS.pinned) return;
		names[id] = label;
		interfaces = [...interfaces, id];
	}
</script>

<div class="picker">
	{#if interfaces.length === 0}<p class="muted">No interface pinned yet.</p>{/if}
	<ol class="pinned">
		{#each interfaces as id, n (id)}
			<li>
				<span>{names[id] ?? `Interface ${id.slice(0, 8)}`}</span>
				<button
					type="button"
					aria-label="Move {names[id] ?? 'interface'} up"
					disabled={n === 0}
					onclick={() => (interfaces = move(interfaces, n, -1))}>↑</button
				>
				<button
					type="button"
					aria-label="Move {names[id] ?? 'interface'} down"
					disabled={n === interfaces.length - 1}
					onclick={() => (interfaces = move(interfaces, n, 1))}>↓</button
				>
				<button
					type="button"
					aria-label="Unpin {names[id] ?? 'interface'}"
					onclick={() => (interfaces = interfaces.filter((x) => x !== id))}>Unpin</button
				>
			</li>
		{/each}
	</ol>
	<!-- Not a form: the picker sits inside the editor's form, and forms cannot nest. -->
	<div class="row">
		<label for="{idPrefix}-find">Find an interface</label>
		<input
			id="{idPrefix}-find"
			type="search"
			maxlength="256"
			bind:value={typed}
			onkeydown={(e) => {
				if (e.key !== 'Enter') return;
				e.preventDefault();
				term = typed.trim();
			}}
		/>
		<button type="button" disabled={typed.trim() === ''} onclick={() => (term = typed.trim())}
			>Search</button
		>
	</div>
	{#if term && found.data}
		{#if hits.length === 0}
			<p class="muted">No interface matches “{term}”.</p>
		{:else}
			<ul class="hits" aria-label="Interfaces found">
				{#each hits as h (h.interface_id)}
					{@const id = h.interface_id ?? ''}
					<li>
						{h.label} <span class="muted">{h.detail}</span>
						<button
							type="button"
							disabled={interfaces.includes(id) || interfaces.length >= LIMITS.pinned}
							onclick={() => add(id, h.label)}>{interfaces.includes(id) ? 'Pinned' : 'Pin'}</button
						>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
	{#if interfaces.length >= LIMITS.pinned}
		<p class="muted">A widget holds at most {LIMITS.pinned} interfaces; add another widget.</p>
	{/if}
</div>

<style>
	.pinned li,
	.hits li {
		margin: 0.2rem 0;
	}
	.pinned button,
	.hits button {
		margin-left: 0.3rem;
	}
</style>
