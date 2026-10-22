<!--
@component
A widget's filters in the dashboard editor: site, tag, group, and for interfaces also provider,
WAN only, and interest (UI-004, DASH-006).

Choices come from the cached catalog (`filterOptions`); "Any" leaves a
filter unset. The criteria object is bound, so the editor's draft holds
exactly what the API is sent.

Example: `<CriteriaFields bind:criteria={w.criteria} interfaces {options} idPrefix="w2" />`
-->
<script lang="ts">
	import type { DeviceCriteriaIn, InterfaceCriteriaIn } from '#lib/api/types.ts';
	import type { FilterOptions } from '#lib/state/filters.ts';

	interface Props {
		criteria: InterfaceCriteriaIn | DeviceCriteriaIn;
		/** Offer the interface-only filters too. */
		interfaces?: boolean;
		options: FilterOptions;
		/** Unique prefix for the fields' IDs. */
		idPrefix: string;
	}

	let { criteria = $bindable(), interfaces = false, options, idPrefix }: Props = $props();
	const iface = $derived(criteria as InterfaceCriteriaIn);
	const lists = $derived([
		['site_id', 'Site', options.sites],
		['tag_id', 'Tag', options.tags],
		['group_id', 'Group', options.groups]
	] as const);

	/**
	 * Set or clear one UUID filter.
	 *
	 * @param key - Which filter.
	 * @param value - The chosen UUID, or an empty string for "Any".
	 */
	function setId(key: 'site_id' | 'tag_id' | 'group_id', value: string): void {
		criteria = { ...criteria, [key]: value === '' ? null : value };
	}

	/**
	 * Set or clear an interface-only filter.
	 *
	 * @param change - The fields to change.
	 */
	function setIface(change: Partial<InterfaceCriteriaIn>): void {
		criteria = { ...iface, ...change };
	}
</script>

<fieldset class="criteria">
	<legend>Filters</legend>
	{#each lists as [key, label, choices] (key)}
		<label for="{idPrefix}-{key}">{label}</label>
		<select
			id="{idPrefix}-{key}"
			value={criteria[key] ?? ''}
			onchange={(e) => setId(key, e.currentTarget.value)}
		>
			<option value="">Any</option>
			{#each choices as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
		</select>
	{/each}
	{#if interfaces}
		<label for="{idPrefix}-provider">Provider</label>
		<input
			id="{idPrefix}-provider"
			maxlength="128"
			value={iface.provider ?? ''}
			oninput={(e) => setIface({ provider: e.currentTarget.value.trim() || null })}
		/>
		<label for="{idPrefix}-interest">Interest</label>
		<select
			id="{idPrefix}-interest"
			value={iface.interest ?? ''}
			onchange={(e) => {
				const v = e.currentTarget.value;
				setIface({ interest: v === 'primary' || v === 'secondary' ? v : null });
			}}
		>
			<option value="">Any</option>
			<option value="primary">High priority</option>
			<option value="secondary">Normal priority</option>
		</select>
		<label class="check">
			<input
				type="checkbox"
				checked={iface.wan_only}
				onchange={(e) => setIface({ wan_only: e.currentTarget.checked })}
			/>
			WAN circuits only
		</label>
	{/if}
</fieldset>

<style>
	.criteria {
		display: grid;
		grid-template-columns: max-content minmax(10rem, 20rem);
		gap: 0.4rem 0.8rem;
		align-items: center;
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.6rem;
	}
	.check {
		grid-column: 1 / -1;
	}
</style>
