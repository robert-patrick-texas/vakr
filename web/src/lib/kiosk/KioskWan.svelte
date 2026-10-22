<!--
@component
WAN circuits on a kiosk display (DASH-002, UI-008, UI-013).

The first page of circuits with provider, circuit ID, contracted rate, and
current use, from the same table as the WAN page. One request per refresh
(DASH-T06).

Example: `<KioskWan settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { wanRead, type BoardSettings } from './boards.ts';
	import { BOARD_TITLES, type RotationPosition } from '#lib/state/kiosk.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import WanTable from '#lib/components/WanTable.svelte';
	import KioskHeader from './KioskHeader.svelte';

	interface Props {
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { settings, position = null, paused = false, onPause }: Props = $props();
	const query = createQuery(() => wanRead(settings));
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
	title={BOARD_TITLES.wan}
	updatedAt={query.dataUpdatedAt}
	refreshMs={wanRead(settings).refetchInterval}
	{position}
	{paused}
	{onPause}
/>
<PageStatus state={shown} label="WAN circuits" emptyText="No interface is marked as a WAN circuit.">
	{#snippet children(d)}
		<WanTable rows={d.rows} position={d.position} />
	{/snippet}
</PageStatus>
