<!--
@component
WAN circuits with provider, circuit ID, contracted rate, and current use (DASH-002, UI-008).

Shared by the WAN page and its kiosk dashboard; on a kiosk display the
device and interface names are plain text (UI-013). Every value is the
API's `Reading`, so a missing rate says why, never 0.

Example: `<WanTable rows={page.rows} position={page.position} />`
-->
<script lang="ts">
	import type { InterfaceListRow } from '#lib/api/types.ts';
	import { bitRate } from '#lib/format/units.ts';
	import { deviceHref, interfaceHref } from '#lib/links.ts';
	import Link from './Link.svelte';
	import ReadingText from './ReadingText.svelte';

	type Circuit = InterfaceListRow;

	interface Props {
		rows: Circuit[];
		/** The position the current values come from. */
		position: string;
	}

	const { rows, position }: Props = $props();
</script>

<div class="table-wrap">
	<table class="density-compact">
		<caption>WAN circuits (current values from {position})</caption>
		<thead>
			<tr>
				<th scope="col">Circuit</th>
				<th scope="col">Provider</th>
				<th scope="col">Device and interface</th>
				<th scope="col">Site</th>
				<th scope="col" class="num">Contracted</th>
				<th scope="col" class="num">In / out</th>
				<th scope="col" class="num">Util. in / out</th>
				<th scope="col">Admin/oper</th>
				<th scope="col" class="num">Age</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.ref.interface_id)}
				<tr>
					<th scope="row">{r.ref.circuit_id ?? '—'}</th>
					<td>{r.ref.provider ?? '—'}</td>
					<td>
						<Link href={deviceHref(r.ref.device_id)}>{r.ref.device}</Link>
						<Link href={interfaceHref(r.ref.interface_id, { from: '/wan' })}>{r.ref.name}</Link>
					</td>
					<td>{r.ref.site ?? '—'}</td>
					<td class="num">{bitRate(r.contracted_bps)}</td>
					<td class="num">
						<ReadingText reading={r.current.in_rate} compact /> /
						<ReadingText reading={r.current.out_rate} compact />
					</td>
					<td class="num">
						<ReadingText reading={r.current.in_util} compact /> /
						<ReadingText reading={r.current.out_util} compact />
					</td>
					<td><ReadingText reading={r.current.state} compact /></td>
					<td class="num"><ReadingText reading={r.current.age} compact /></td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
