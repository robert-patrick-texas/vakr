<!--
@component
One built-in dashboard on a kiosk display, by name (UI-013).

The single-dashboard pages and the rotation both render through this, so
a dashboard looks and behaves the same in either. Only the dashboard
named is mounted, so only its reads poll (DASH-T06).

Example: `<KioskBoardView board="wan" settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import type { BoardSettings } from './boards.ts';
	import type { KioskBoard, RotationPosition } from '#lib/state/kiosk.ts';
	import KioskHighPriority from './KioskHighPriority.svelte';
	import KioskOverview from './KioskOverview.svelte';
	import KioskRankings from './KioskRankings.svelte';
	import KioskWan from './KioskWan.svelte';

	interface Props {
		board: KioskBoard;
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { board, settings, position = null, paused = false, onPause }: Props = $props();
</script>

<div data-kiosk-board={board}>
	{#if board === 'overview'}
		<KioskOverview {settings} {position} {paused} {onPause} />
	{:else if board === 'high-priority'}
		<KioskHighPriority {settings} {position} {paused} {onPause} />
	{:else if board === 'wan'}
		<KioskWan {settings} {position} {paused} {onPause} />
	{:else}
		<KioskRankings {settings} {position} {paused} {onPause} />
	{/if}
</div>
