<!--
@component
An interface's identity decisions, oldest first, with prior `ifIndex` values (12.5, IFID-007).

Each decision says when, how (automatic or by whom), the outcome, the
`ifIndex` before and after, the match scores, and the evidence, so a
renumbered interface's history is explained.

Example: `<IdentityHistory interfaceId={id} zone="UTC" />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { formatInstant } from '#lib/format/time.ts';
	import { identityHistoryQuery } from '#lib/query/reads.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from './PageStatus.svelte';

	interface Props {
		interfaceId: string;
		zone: string;
	}

	const { interfaceId, zone }: Props = $props();
	const query = createQuery(() => identityHistoryQuery(interfaceId));
	const shown = $derived(pageState(query, { isEmpty: (d) => d.items.length === 0 }));
</script>

<PageStatus state={shown} label="Identity history" emptyText="No identity decision is recorded.">
	{#snippet children(d)}
		<div class="table-wrap">
			<table class="density-compact">
				<caption class="sr-only">Identity decisions, oldest first</caption>
				<thead>
					<tr>
						<th scope="col">Decided</th>
						<th scope="col">Outcome</th>
						<th scope="col">By</th>
						<th scope="col" class="num">ifIndex before → after</th>
						<th scope="col" class="num">Score (runner-up)</th>
						<th scope="col">Evidence</th>
					</tr>
				</thead>
				<tbody>
					{#each d.items as e (e.id)}
						<tr>
							<td>{formatInstant(e.decided_at, zone)}</td>
							<td>{e.outcome}</td>
							<td>{e.actor ?? e.source}</td>
							<td class="num">{e.previous_if_index ?? '—'} → {e.observed_if_index ?? '—'}</td>
							<td class="num">{e.score ?? '—'} ({e.runner_up_score ?? '—'})</td>
							<td>{e.evidence.join('; ') || '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/snippet}
</PageStatus>
