<!--
@component
High-priority interfaces on a kiosk display (UI-008, UI-013, META-011).

The first page of the API's priority order (up to 100 interfaces), every
column of the normal page, names as plain text. One request per refresh
(DASH-T06).

Example: `<KioskHighPriority settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { highPriorityRead, type BoardSettings } from './boards.ts';
	import { BOARD_TITLES, type RotationPosition } from '#lib/state/kiosk.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import InterfaceTable from '#lib/components/InterfaceTable.svelte';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import KioskHeader from './KioskHeader.svelte';

	interface Props {
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { settings, position = null, paused = false, onPause }: Props = $props();
	const query = createQuery(() => highPriorityRead(settings));
	const shown = $derived(
		pageState(query, {
			isEmpty: (d) => d.rows.length === 0,
			partial: (d) => [
				...(d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]),
				...(d.page.next_cursor ? [`Showing the first ${d.rows.length} of ${d.page.total}.`] : [])
			]
		})
	);
</script>

<KioskHeader
	title={BOARD_TITLES['high-priority']}
	updatedAt={query.dataUpdatedAt}
	refreshMs={highPriorityRead(settings).refetchInterval}
	{position}
	{paused}
	{onPause}
/>
<PageStatus
	state={shown}
	label="High-priority interfaces"
	emptyText="No interface is marked high priority (interest = primary)."
>
	{#snippet children(d)}
		<InterfaceTable caption="High-priority interfaces" hideCaption rows={d.rows} showDevice />
	{/snippet}
</PageStatus>
