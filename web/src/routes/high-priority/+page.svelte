<!--
@component
High-priority interfaces: every primary-interest interface in priority order (UI-008, META-011).

Paged by the API, refreshed every 30 s, with current values from the local
collector unless another position is chosen.
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { highPriorityQuery } from '#lib/query/reads.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';

	const trail = new CursorTrail();
	const query = createQuery(() => highPriorityQuery(trail.cursor, 100));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.rows.length === 0,
			partial: (d) =>
				d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]
		})
	);
</script>

<svelte:head><title>High-priority interfaces · vakr</title></svelte:head>

<h1>High-priority interfaces</h1>
<PageStatus
	state={shown}
	label="High-priority interfaces"
	emptyText="No interface is marked high priority (interest = primary)."
>
	{#snippet children(d)}
		<InterfaceTable
			caption="High-priority interfaces"
			hideCaption
			rows={d.rows}
			showDevice
			keep={{ from: '/high-priority' }}
		/>
		<Pager
			page={d.page}
			shown={d.rows.length}
			pageNumber={trail.number}
			noun="interfaces"
			onNext={trail.next}
			onPrevious={trail.previous}
			onFirst={trail.reset}
		/>
	{/snippet}
</PageStatus>
