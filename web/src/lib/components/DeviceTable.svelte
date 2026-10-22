<!--
@component
The device list's columns as 12.3 requires them (UI-005, WEB-T01).

State is colour, icon, and words together; the rows keep the API's order:
Attention, then Active, then Offline/Retired, then severity and natural
name (7.7). Sortable headers ask the API for its other orders (name, site,
age). Values from stale evidence say "(stale)" in text; a missing value
says why.

Example: `<DeviceTable rows={page.rows} sort="status" onSort={setSort} />`
-->
<script lang="ts">
	import type { DeviceRow } from '#lib/api/types.ts';
	import { deviceHref } from '#lib/links.ts';
	import DataTable from './DataTable.svelte';
	import Link from './Link.svelte';
	import ReadingText from './ReadingText.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import type { Column } from './table.ts';

	interface Props {
		rows: DeviceRow[];
		sort?: string | null;
		onSort?: (key: string) => void;
		compact?: boolean;
		caption?: string;
	}

	const { rows, sort = null, onSort, compact = false, caption = 'Devices' }: Props = $props();
</script>

{#snippet status(r: DeviceRow)}
	{@const seen = r.positions.filter((p) => p.age.value !== null)}
	<StatusBadge color={r.status.color} icon={r.status.icon} label={r.status.label} small />
	{#if seen.length > 1}
		<div class="positions small">
			{#each seen as p (p.collector_id)}
				<span>{p.position}: {p.status.icon} {p.status.label}</span>
			{/each}
		</div>
	{/if}
{/snippet}
{#snippet name(r: DeviceRow)}<Link href={deviceHref(r.device_id)}>{r.name}</Link>{/snippet}
{#snippet address(r: DeviceRow)}<span class="mono">{r.address}</span>{/snippet}
{#snippet site(r: DeviceRow)}<ReadingText reading={r.site} compact />{/snippet}
{#snippet icmp(r: DeviceRow)}
	<ReadingText reading={r.icmp} compact />
	<div class="small"><ReadingText reading={r.loss} compact /> loss</div>
{/snippet}
{#snippet snmp(r: DeviceRow)}<ReadingText reading={r.snmp} compact />{/snippet}
{#snippet uptime(r: DeviceRow)}<ReadingText reading={r.uptime} compact />{/snippet}
{#snippet interfaces(r: DeviceRow)}<ReadingText reading={r.interfaces} compact />{/snippet}
{#snippet lastPoll(r: DeviceRow)}<ReadingText reading={r.last_poll} compact />{/snippet}
{#snippet age(r: DeviceRow)}
	<ReadingText reading={r.age} compact />
	<div class="small"><ReadingText reading={r.freshness} compact /></div>
{/snippet}
{#snippet collector(r: DeviceRow)}<ReadingText reading={r.collector} compact />{/snippet}
{#snippet tags(r: DeviceRow)}{r.tags.join(', ') || '—'}{/snippet}
{#snippet interval(r: DeviceRow)}<ReadingText reading={r.interval} compact />{/snippet}

<DataTable
	{caption}
	{rows}
	{sort}
	{onSort}
	{compact}
	rowKey={(r) => r.device_id}
	columns={[
		{
			id: 'status',
			header: 'State',
			cell: status,
			sortKey: 'status',
			sortDirection: 'ascending',
			sortWords: 'Attention first, then Active, then Offline'
		},
		{
			id: 'name',
			header: 'Name',
			cell: name,
			sortKey: 'name',
			sortDirection: 'ascending',
			sortWords: 'A to Z'
		},
		{ id: 'address', header: 'Poll address', cell: address },
		{
			id: 'site',
			header: 'Site',
			cell: site,
			sortKey: 'site',
			sortDirection: 'ascending',
			sortWords: 'A to Z, devices without a site last'
		},
		{ id: 'icmp', header: 'ICMP / loss', cell: icmp },
		{ id: 'snmp', header: 'SNMP', cell: snmp },
		{ id: 'uptime', header: 'Agent uptime', cell: uptime, numeric: true },
		{ id: 'interfaces', header: 'Interfaces active/total', cell: interfaces, numeric: true },
		{ id: 'last', header: 'Last poll', cell: lastPoll },
		{
			id: 'age',
			header: 'Evidence age',
			cell: age,
			numeric: true,
			sortKey: 'age',
			sortDirection: 'descending',
			sortWords: 'never observed and oldest first'
		},
		{ id: 'collector', header: 'Collector', cell: collector },
		{ id: 'tags', header: 'Tags', cell: tags },
		{ id: 'interval', header: 'Interval', cell: interval, numeric: true }
	] satisfies Column<DeviceRow>[]}
/>

<style>
	.small {
		font-size: 0.85em;
	}
	.positions {
		display: grid;
		margin-top: 0.15rem;
	}
</style>
