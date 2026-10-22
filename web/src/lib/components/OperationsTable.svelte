<!--
@component
Recent admin operations on the Admin page: manual rediscoveries, newest first (API-011, E.32).

Shows the newest 25 with when they were asked for, the device, who asked,
and where each is or how it ended. The list refreshes every 2 s while any
row is queued or running, otherwise on the list interval.

Example: `<OperationsTable />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { formatInstant } from '#lib/format/time.ts';
	import { deviceHref } from '#lib/links.ts';
	import { outcomeText } from '#lib/operations/words.ts';
	import { operationsQuery } from '#lib/query/reads-operations.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from './PageStatus.svelte';

	const SHOWN = 25;
	const ops = createQuery(() => operationsQuery(null, SHOWN));
	const shown = $derived(pageState(ops, { isEmpty: (d) => d.rows.length === 0 }));
</script>

<section aria-labelledby="ops-h">
	<h2 id="ops-h">Recent operations</h2>
	<PageStatus state={shown} label="Operations" emptyText="No rediscovery has been requested yet.">
		{#snippet children(d)}
			<div class="table-wrap">
				<table class="density-compact" data-testid="operations">
					<caption class="sr-only">Recent admin operations, newest first</caption>
					<thead>
						<tr>
							<th scope="col">Requested (UTC)</th>
							<th scope="col">Device</th>
							<th scope="col">By</th>
							<th scope="col">Outcome</th>
						</tr>
					</thead>
					<tbody>
						{#each d.rows as op (op.id)}
							<tr data-state={op.state}>
								<td class="mono">{formatInstant(op.requested_at, 'UTC')}</td>
								<td><a href={deviceHref(op.device_id)}>{op.device_name ?? op.device_id}</a></td>
								<td>{op.requested_by}</td>
								<td>{outcomeText(op)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if (d.page.total ?? 0) > SHOWN}
				<p class="muted">
					The newest {SHOWN} of {d.page.total}; <code>vakr inventory operations</code> lists more.
				</p>
			{/if}
		{/snippet}
	</PageStatus>
</section>
