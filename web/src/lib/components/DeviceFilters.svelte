<!--
@component
The device list's controls (12.3): search; status, freshness, site, tag, group, collector,
provider, and admin-state filters; retired devices; page size; density.

Every control is a labelled native form control, so it is reachable and
operable by keyboard. A change calls `onChange` with the whole filter set;
the page writes it to the URL, so the view can be bookmarked.

Example: `<DeviceFilters filters={f} options={o} onChange={apply} />`
-->
<script lang="ts">
	import type { DeviceQuery } from '#lib/query/reads.ts';
	import { PAGE_SIZES, type FilterOptions } from '#lib/state/filters.ts';

	interface Props {
		filters: DeviceQuery;
		options: FilterOptions;
		compact: boolean;
		onChange: (next: DeviceQuery, compact: boolean) => void;
	}

	const { filters, options, compact, onChange }: Props = $props();
	// svelte-ignore state_referenced_locally
	let search = $state(filters.search ?? '');
	const buckets = [
		['attention', 'Attention'],
		['active', 'Active'],
		['offline', 'Offline/Retired']
	] as const;
	const freshness = [
		['fresh', 'Fresh'],
		['stale', 'Stale'],
		['never', 'Never observed']
	] as const;

	/**
	 * Apply one changed field.
	 *
	 * @param patch - The changed filters.
	 */
	function set(patch: Partial<DeviceQuery>): void {
		onChange({ ...filters, ...patch }, compact);
	}

	/**
	 * Toggle one value of a repeatable filter.
	 *
	 * @param list - Current values.
	 * @param value - The value toggled.
	 * @returns The new list.
	 */
	function toggle<T>(list: T[] | undefined, value: T): T[] {
		const now = list ?? [];
		return now.includes(value) ? now.filter((v) => v !== value) : [...now, value];
	}
</script>

<form
	class="filters"
	aria-label="Device filters"
	onsubmit={(e) => {
		e.preventDefault();
		set({ search: search.trim() });
	}}
>
	<div class="row">
		<label>Search <input type="search" maxlength="256" bind:value={search} /></label>
		<button type="submit">Apply</button>
	</div>
	<fieldset class="row">
		<legend>Status</legend>
		{#each buckets as [value, text] (value)}
			<label
				><input
					type="checkbox"
					checked={filters.bucket?.includes(value)}
					onchange={() => set({ bucket: toggle(filters.bucket, value) })}
				/>
				{text}</label
			>
		{/each}
	</fieldset>
	<fieldset class="row">
		<legend>Freshness</legend>
		{#each freshness as [value, text] (value)}
			<label
				><input
					type="checkbox"
					checked={filters.freshness?.includes(value)}
					onchange={() => set({ freshness: toggle(filters.freshness, value) })}
				/>
				{text}</label
			>
		{/each}
	</fieldset>
	<div class="row">
		{#each [['site', 'Site', options.sites], ['tag', 'Tag', options.tags], ['group', 'Group', options.groups], ['collector', 'Collector', options.collectors]] as const as [key, text, list] (key)}
			<label
				>{text}
				<select
					value={filters[key] ?? ''}
					onchange={(e) => set({ [key]: e.currentTarget.value || null })}
				>
					<option value="">Any</option>
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
			>Admin state
			<select
				value={filters.admin_state ?? ''}
				onchange={(e) =>
					set({ admin_state: (e.currentTarget.value || null) as DeviceQuery['admin_state'] })}
			>
				<option value="">Any</option>
				{#each ['enabled', 'maintenance', 'disabled', 'retired'] as s (s)}<option value={s}
						>{s}</option
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
		<label
			>Rows per page
			<select
				value={String(filters.limit)}
				onchange={(e) => set({ limit: Number(e.currentTarget.value) })}
			>
				{#each PAGE_SIZES as n (n)}<option value={String(n)}>{n}</option>{/each}
			</select></label
		>
		<label
			><input
				type="checkbox"
				checked={compact}
				onchange={(e) => onChange(filters, e.currentTarget.checked)}
			/> Compact rows</label
		>
	</div>
</form>

<style>
	.filters {
		display: grid;
		gap: 0.4rem;
		margin-bottom: 0.6rem;
	}
	fieldset {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.2rem 0.6rem;
		margin: 0;
	}
	legend {
		font-weight: 600;
		padding: 0 0.3rem;
	}
	label {
		display: inline-flex;
		gap: 0.3rem;
		align-items: center;
	}
</style>
