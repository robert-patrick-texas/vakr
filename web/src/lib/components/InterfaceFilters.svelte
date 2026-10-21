<!--
@component
The interface list's controls (12.5): search; site, tag, group, provider, WAN, interest, and
lifecycle filters; retired interfaces; the position whose values are shown; page size.

Example: `<InterfaceFilters filters={f} options={o} onChange={apply} />`
-->
<script lang="ts">
	import type { InterfaceQuery } from '#lib/query/reads.ts';
	import { PAGE_SIZES, type FilterOptions } from '#lib/state/filters.ts';

	interface Props {
		filters: InterfaceQuery;
		options: FilterOptions;
		onChange: (next: InterfaceQuery) => void;
		/** Rankings have no search text or page size. */
		ranking?: boolean;
	}

	const { filters, options, onChange, ranking = false }: Props = $props();
	// svelte-ignore state_referenced_locally
	let search = $state(filters.search ?? '');

	/**
	 * Apply one changed field.
	 *
	 * @param patch - The changed filters.
	 */
	function set(patch: Partial<InterfaceQuery>): void {
		onChange({ ...filters, ...patch });
	}
</script>

<form
	class="filters"
	aria-label="Interface filters"
	onsubmit={(e) => {
		e.preventDefault();
		set({ search: search.trim() });
	}}
>
	{#if !ranking}
		<div class="row">
			<label
				>Search name, alias, description, provider, circuit
				<input type="search" maxlength="256" bind:value={search} /></label
			>
			<button type="submit">Apply</button>
		</div>
	{/if}
	<div class="row">
		{#each [['site', 'Site', options.sites], ['tag', 'Tag', options.tags], ['group', 'Group', options.groups], ['collector', 'Values from', options.collectors]] as const as [key, text, list] (key)}
			<label
				>{text}
				<select
					value={filters[key] ?? ''}
					onchange={(e) => set({ [key]: e.currentTarget.value || null })}
				>
					<option value="">{key === 'collector' ? 'Local collector' : 'Any'}</option>
					{#each list as item (item.id)}<option value={item.id}>{item.name}</option>{/each}
				</select></label
			>
		{/each}
		<label
			>Provider
			<input
				value={filters.provider ?? ''}
				maxlength="256"
				onchange={(e) => set({ provider: e.currentTarget.value.trim() || null })}
			/></label
		>
		<label
			>WAN
			<select
				value={filters.wan === true ? 'true' : filters.wan === false ? 'false' : ''}
				onchange={(e) => {
					const v = e.currentTarget.value;
					set({ wan: v === 'true' ? true : v === 'false' ? false : undefined });
				}}
			>
				<option value="">Any</option>
				<option value="true">WAN circuits only</option>
				<option value="false">Not WAN circuits</option>
			</select></label
		>
		<label
			>Interest
			<select
				value={filters.interest ?? ''}
				onchange={(e) =>
					set({ interest: (e.currentTarget.value || null) as InterfaceQuery['interest'] })}
			>
				<option value="">Any</option>
				<option value="primary">High priority</option>
				<option value="secondary">Other</option>
			</select></label
		>
		<label
			>Lifecycle
			<select
				value={filters.lifecycle ?? ''}
				onchange={(e) =>
					set({ lifecycle: (e.currentTarget.value || null) as InterfaceQuery['lifecycle'] })}
			>
				<option value="">Any but retired</option>
				{#each ['active', 'inactive', 'stale', 'retired'] as l (l)}<option value={l}>{l}</option
					>{/each}
			</select></label
		>
		<label
			><input
				type="checkbox"
				checked={filters.include_retired}
				onchange={(e) => set({ include_retired: e.currentTarget.checked })}
			/> Include retired</label
		>
		{#if !ranking}
			<label
				>Rows per page
				<select
					value={String(filters.limit)}
					onchange={(e) => set({ limit: Number(e.currentTarget.value) })}
				>
					{#each PAGE_SIZES as n (n)}<option value={String(n)}>{n}</option>{/each}
				</select></label
			>
		{/if}
	</div>
</form>

<style>
	.filters {
		display: grid;
		gap: 0.4rem;
		margin-bottom: 0.6rem;
	}
	label {
		display: inline-flex;
		gap: 0.3rem;
		align-items: center;
	}
</style>
