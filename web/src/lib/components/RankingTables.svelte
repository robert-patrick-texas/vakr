<!--
@component
Top utilization or top errors for a window (DASH-003, DASH-004, DASH-005; UI-008).

Utilization ranks by the higher directional p95 of the window against the
interface's capacity (named with its source); errors rank by valid error
deltas, with errors per million packets when known. Each row says how many
samples it rests on, and a row with fewer than expected is marked partial
in words, so incomplete coverage never looks exact (UI-015). Interfaces
left out of the ranking are counted with the reason.

Example: `<RankingTables kind="utilization" view={ranking} />`
-->
<script lang="ts">
	import type { ErrorRanking, UtilizationRanking } from '#lib/api/types.ts';
	import { interfaceHref } from '#lib/links.ts';
	import { bitRate, count, percent } from '#lib/format/units.ts';
	import Link from './Link.svelte';

	type Props =
		| { kind: 'utilization'; view: UtilizationRanking; limit?: number }
		| { kind: 'errors'; view: ErrorRanking; limit?: number };

	const props: Props = $props();
	const limit = $derived(props.limit ?? 10);
</script>

{#snippet coverage(c: { valid: number; expected: number })}
	{c.valid} of {c.expected}{#if c.valid < c.expected}<strong class="partial">
			(partial)</strong
		>{/if}
{/snippet}

{#snippet subject(ref: { interface_id: string; device: string; name: string })}
	<Link href={interfaceHref(ref.interface_id)}>{ref.device} {ref.name}</Link>
{/snippet}

<div class="table-wrap">
	<table class="density-compact">
		{#if props.kind === 'utilization'}
			<caption>Top utilization, p95 over {props.view.window_name} ({props.view.position})</caption>
			<thead>
				<tr>
					<th scope="col" class="num">#</th>
					<th scope="col">Interface</th>
					<th scope="col" class="num">p95 in</th>
					<th scope="col" class="num">p95 out</th>
					<th scope="col">Capacity</th>
					<th scope="col">Samples</th>
				</tr>
			</thead>
			<tbody>
				{#each props.view.rows.slice(0, limit) as r (r.interface.interface_id)}
					<tr>
						<td class="num">{r.rank}</td>
						<td>{@render subject(r.interface)}</td>
						<td class="num"
							>{percent(r.in_p95_pct)} <span class="muted">{bitRate(r.in_p95_bps)}</span></td
						>
						<td class="num"
							>{percent(r.out_p95_pct)} <span class="muted">{bitRate(r.out_p95_bps)}</span></td
						>
						<td>{bitRate(r.capacity_bps)} <span class="muted">({r.capacity_source})</span></td>
						<td>{@render coverage(r.coverage)}</td>
					</tr>
				{/each}
			</tbody>
		{:else}
			<caption>Top errors over {props.view.window_name} ({props.view.position})</caption>
			<thead>
				<tr>
					<th scope="col" class="num">#</th>
					<th scope="col">Interface</th>
					<th scope="col" class="num">Errors (in / out)</th>
					<th scope="col" class="num">Per million packets</th>
					<th scope="col">Samples</th>
				</tr>
			</thead>
			<tbody>
				{#each props.view.rows.slice(0, limit) as r (r.interface.interface_id)}
					<tr>
						<td class="num">{r.rank}</td>
						<td>{@render subject(r.interface)}</td>
						<td class="num">{count(r.errors)} ({count(r.in_errors)} / {count(r.out_errors)})</td>
						<td class="num">{r.per_million === null ? '—' : count(r.per_million)}</td>
						<td>{@render coverage(r.coverage)}</td>
					</tr>
				{/each}
			</tbody>
		{/if}
	</table>
</div>
{#if props.view.rows.length === 0 && props.view.available}
	<p class="muted">No interface qualifies in this window.</p>
{/if}
{#if props.view.excluded_total > 0}
	<details>
		<summary>{props.view.excluded_total} interface(s) left out</summary>
		<ul class="small">
			{#each props.view.excluded as e (e.interface.interface_id)}
				<li>{e.interface.device} {e.interface.name}: {e.reason}</li>
			{/each}
		</ul>
	</details>
{/if}
{#each props.view.notes as note (note)}<p class="muted small">{note}</p>{/each}

<style>
	.partial {
		font-weight: 600;
	}
	.small {
		font-size: 0.85rem;
	}
</style>
