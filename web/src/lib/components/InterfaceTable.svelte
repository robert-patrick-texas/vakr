<!--
@component
Interface rows as 12.5 lists them: indicators, names, state, rates, utilization, packets, errors,
capacity, addresses, and age (UI-002, UI-006).

The rows come in the API's order: sticky, WAN circuits, and high-priority
interfaces first (UI-006), unless the user sorts by name, traffic, or
utilization. Every value is the API's `Reading`: a missing rate says why
("first sample; a rate needs two"), never 0. Indicators are words, not
only icons. `showDevice` adds the device column for the global list; `onSelect` adds a
labelled checkbox per row for bulk tagging (META-013).

Example: `<InterfaceTable rows={page.rows} sort="priority" onSort={setSort} showDevice />`
-->
<script lang="ts">
	import type { InterfaceListRow } from '#lib/api/types.ts';
	import { deviceHref, interfaceHref } from '#lib/links.ts';
	import DataTable from './DataTable.svelte';
	import Link from './Link.svelte';
	import ReadingText from './ReadingText.svelte';
	import type { Column } from './table.ts';

	interface Props {
		rows: InterfaceListRow[];
		caption?: string;
		sort?: string | null;
		onSort?: (key: string) => void;
		showDevice?: boolean;
		compact?: boolean;
		hideCaption?: boolean;
		/** Extra query parameters kept on links to the detail page (WEB-T03: filters survive). */
		keep?: Record<string, string>;
		/** Selected interface UUIDs, with `onSelect`. */
		selected?: string[];
		/** Adds the selection column; called when a row's checkbox changes. */
		onSelect?: (row: InterfaceListRow, on: boolean) => void;
	}

	const {
		rows,
		caption = 'Interfaces',
		sort = null,
		onSort,
		showDevice = false,
		compact = false,
		hideCaption = false,
		keep = {},
		selected = [],
		onSelect
	}: Props = $props();

	const MARKER_WORDS: Record<string, string> = {
		sticky: 'Sticky',
		WAN: 'WAN',
		primary: 'High priority'
	};
</script>

{#snippet pick(r: InterfaceListRow)}
	<input
		type="checkbox"
		aria-label="Select {r.ref.device} {r.ref.name}"
		checked={selected.includes(r.ref.interface_id)}
		onchange={(e) => onSelect?.(r, e.currentTarget.checked)}
	/>
{/snippet}
{#snippet indicators(r: InterfaceListRow)}
	{#each r.ref.markers as m (m)}<span class="tag">{MARKER_WORDS[m] ?? m}</span>{/each}
{/snippet}
{#snippet device(r: InterfaceListRow)}<Link href={deviceHref(r.ref.device_id)}>{r.ref.device}</Link
	>{/snippet}
{#snippet name(r: InterfaceListRow)}
	<Link href={interfaceHref(r.ref.interface_id, keep)}>{r.ref.name}</Link>
	{#if r.ref.alias}<div class="muted small">{r.ref.alias}</div>{/if}
	{#if r.ref.description && r.ref.description !== r.ref.name}<div class="muted small">
			{r.ref.description}
		</div>{/if}
	{#if r.ref.provider || r.ref.circuit_id}<div class="small">
			{[r.ref.provider, r.ref.circuit_id].filter(Boolean).join(' · ')}
		</div>{/if}
{/snippet}
{#snippet state(r: InterfaceListRow)}<ReadingText reading={r.current.state} compact />{/snippet}
{#snippet lifecycle(r: InterfaceListRow)}{r.current.lifecycle}{/snippet}
{#snippet inRate(r: InterfaceListRow)}<ReadingText reading={r.current.in_rate} compact />{/snippet}
{#snippet outRate(r: InterfaceListRow)}<ReadingText
		reading={r.current.out_rate}
		compact
	/>{/snippet}
{#snippet util(r: InterfaceListRow)}
	<ReadingText reading={r.current.in_util} compact /> /
	<ReadingText reading={r.current.out_util} compact />
{/snippet}
{#snippet pps(r: InterfaceListRow)}
	<ReadingText reading={r.current.in_pps} compact /> /
	<ReadingText reading={r.current.out_pps} compact />
{/snippet}
{#snippet errors(r: InterfaceListRow)}<ReadingText reading={r.current.errors} compact />{/snippet}
{#snippet capacity(r: InterfaceListRow)}<ReadingText
		reading={r.current.capacity}
		compact
	/>{/snippet}
{#snippet addresses(r: InterfaceListRow)}<span class="mono small"
		>{r.addresses.join(', ') || '—'}</span
	>{/snippet}
{#snippet age(r: InterfaceListRow)}<ReadingText reading={r.current.age} compact />{/snippet}

<DataTable
	{caption}
	{rows}
	{sort}
	{onSort}
	{compact}
	{hideCaption}
	rowKey={(r) => r.ref.interface_id}
	columns={[
		...(onSelect ? [{ id: 'select', header: 'Select', cell: pick }] : []),
		{
			id: 'indicators',
			header: 'Indicators',
			cell: indicators,
			sortKey: 'priority',
			sortWords: 'sticky, WAN, and high priority first'
		},
		...(showDevice ? [{ id: 'device', header: 'Device', cell: device }] : []),
		{
			id: 'name',
			header: 'Interface',
			cell: name,
			sortKey: 'name',
			sortDirection: 'ascending',
			sortWords: 'A to Z'
		},
		{ id: 'state', header: 'Admin/oper', cell: state },
		{ id: 'lifecycle', header: 'Lifecycle', cell: lifecycle },
		{
			id: 'in',
			header: 'In bit/s',
			cell: inRate,
			numeric: true,
			sortKey: 'traffic',
			sortDirection: 'descending',
			sortWords: 'busiest first'
		},
		{ id: 'out', header: 'Out bit/s', cell: outRate, numeric: true },
		{
			id: 'util',
			header: 'Util. in / out',
			cell: util,
			numeric: true,
			sortKey: 'utilization',
			sortDirection: 'descending',
			sortWords: 'highest utilization first'
		},
		{ id: 'pps', header: 'In / out pps', cell: pps, numeric: true },
		{ id: 'errors', header: 'Errors', cell: errors, numeric: true },
		{ id: 'capacity', header: 'Capacity', cell: capacity },
		...(compact ? [] : [{ id: 'addresses', header: 'Addresses', cell: addresses }]),
		{ id: 'age', header: 'Age', cell: age, numeric: true }
	] satisfies Column<InterfaceListRow>[]}
/>

<style>
	.tag {
		display: inline-block;
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.3rem;
		margin: 0 0.2rem 0.1rem 0;
		font-size: 0.8em;
		white-space: nowrap;
	}
	.small {
		font-size: 0.85em;
	}
</style>
