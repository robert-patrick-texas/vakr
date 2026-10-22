<!--
@component
A pinned-interfaces widget's table, in the admin's order (UI-004).

Each pinned interface keeps its row. A live one shows its current values
from the API (`Reading`: a missing rate says why, never 0); a retired one
shows its names and "retired"; one that no longer exists says so. Nothing
disappears silently. On a kiosk display names are plain text and the
table's scroll area takes keyboard focus (`scrollRegion`).

Example: `<PinnedTable title="Uplinks" entries={widget.entries} />`
-->
<script lang="ts">
	import type { PinnedEntry } from '#lib/api/types.ts';
	import { deviceHref, interfaceHref } from '#lib/links.ts';
	import { inKiosk, scrollRegion } from '#lib/state/kiosk-context.ts';
	import Link from '#lib/components/Link.svelte';
	import ReadingText from '#lib/components/ReadingText.svelte';

	interface Props {
		title: string;
		entries: PinnedEntry[];
	}

	const { title, entries }: Props = $props();
	const kiosk = inKiosk();
</script>

<div class="table-wrap" {...scrollRegion(kiosk, title)}>
	<table class="density-compact">
		<caption class="sr-only">{title}</caption>
		<thead>
			<tr>
				<th scope="col">Interface</th>
				<th scope="col">Admin/oper</th>
				<th scope="col" class="num">In bit/s</th>
				<th scope="col" class="num">Out bit/s</th>
				<th scope="col" class="num">Util. in / out</th>
				<th scope="col" class="num">Errors</th>
				<th scope="col" class="num">Age</th>
			</tr>
		</thead>
		<tbody>
			{#each entries as e (e.interface_id)}
				<tr data-pinned={e.note ?? 'live'}>
					{#if e.row}
						{@const r = e.row}
						<th scope="row">
							<Link href={deviceHref(r.ref.device_id)}>{r.ref.device}</Link>
							<Link href={interfaceHref(r.ref.interface_id)}>{r.ref.name}</Link>
							{#if r.ref.alias}<div class="muted small">{r.ref.alias}</div>{/if}
						</th>
						<td><ReadingText reading={r.current.state} compact /></td>
						<td class="num"><ReadingText reading={r.current.in_rate} compact /></td>
						<td class="num"><ReadingText reading={r.current.out_rate} compact /></td>
						<td class="num">
							<ReadingText reading={r.current.in_util} compact /> /
							<ReadingText reading={r.current.out_util} compact />
						</td>
						<td class="num"><ReadingText reading={r.current.errors} compact /></td>
						<td class="num"><ReadingText reading={r.current.age} compact /></td>
					{:else}
						<th scope="row">
							{#if e.ref}{e.ref.device} {e.ref.name}{:else}Interface
								<span class="mono small">{e.interface_id.slice(0, 8)}</span>{/if}
						</th>
						<td colspan="6" class="muted">
							{e.note === 'retired'
								? 'Retired: no current values. An admin can remove it from this dashboard.'
								: 'No longer exists. An admin can remove it from this dashboard.'}
						</td>
					{/if}
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.small {
		font-size: 0.85em;
	}
</style>
