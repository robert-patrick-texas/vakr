<!--
@component
One widget in the dashboard editor: its title, kind-specific settings, and its place (UI-004).

Pinned interfaces get the picker and a graphs switch; selections get
filters and a row limit; rankings get the kind, window, row limit, and
filters. Bounds are those of E.31 (`LIMITS`); the API checks them again.

Example: `<WidgetEditor bind:widget={draft.widgets[n]} index={n} count={n2} ... />`
-->
<script lang="ts">
	import type { WidgetIn } from '#lib/api/types.ts';
	import type { FilterOptions } from '#lib/state/filters.ts';
	import CriteriaFields from './CriteriaFields.svelte';
	import PinPicker from './PinPicker.svelte';
	import { KIND_NAMES, LIMITS } from './editor.ts';

	interface Props {
		widget: WidgetIn;
		index: number;
		count: number;
		options: FilterOptions;
		names: Record<string, string>;
		onMove: (delta: number) => void;
		onRemove: () => void;
	}

	let { widget = $bindable(), index, count, options, names, onMove, onRemove }: Props = $props();
	const id = $derived(`w${index}`);
	const WINDOWS = ['15m', '1h', '6h', '24h'] as const;
</script>

<fieldset class="widget-editor card" data-widget-editor={widget.kind}>
	<legend>Widget {index + 1}: {KIND_NAMES[widget.kind]}</legend>
	<div class="row">
		<label for="{id}-title">Title</label>
		<input id="{id}-title" maxlength={LIMITS.title} required bind:value={widget.title} />
		<button
			type="button"
			disabled={index === 0}
			onclick={() => onMove(-1)}
			aria-label="Move widget {index + 1} up">↑</button
		>
		<button
			type="button"
			disabled={index === count - 1}
			onclick={() => onMove(1)}
			aria-label="Move widget {index + 1} down">↓</button
		>
		<button type="button" onclick={onRemove}>Remove widget</button>
	</div>
	{#if widget.kind === 'pinned_interfaces'}
		<PinPicker bind:interfaces={widget.interfaces} {names} idPrefix={id} />
		<label class="check">
			<input type="checkbox" bind:checked={widget.graphs} /> Show a graph card per interface
		</label>
	{:else if widget.kind === 'interface_selection' || widget.kind === 'device_selection'}
		<CriteriaFields
			bind:criteria={widget.criteria}
			interfaces={widget.kind === 'interface_selection'}
			{options}
			idPrefix={id}
		/>
		<label for="{id}-limit">Rows shown</label>
		<input
			id="{id}-limit"
			type="number"
			min="1"
			max={LIMITS.selectionRows}
			bind:value={widget.limit}
		/>
	{:else}
		<div class="row">
			<label for="{id}-ranking">Ranking</label>
			<select id="{id}-ranking" bind:value={widget.ranking}>
				<option value="utilization">Top utilization (p95)</option>
				<option value="errors">Top errors</option>
			</select>
			<label for="{id}-window">Window</label>
			<select id="{id}-window" bind:value={widget.window}>
				{#each WINDOWS as w (w)}<option value={w}>{w}</option>{/each}
			</select>
			<label for="{id}-limit">Rows</label>
			<input
				id="{id}-limit"
				type="number"
				min="1"
				max={LIMITS.rankingRows}
				bind:value={widget.limit}
			/>
		</div>
		<!-- A new ranking and every stored one carry criteria; the API type only marks them optional. -->
		{#if widget.criteria}
			<CriteriaFields bind:criteria={widget.criteria} interfaces {options} idPrefix={id} />
		{/if}
	{/if}
</fieldset>

<style>
	.widget-editor {
		display: grid;
		gap: 0.6rem;
		margin-bottom: var(--gap);
	}
</style>
