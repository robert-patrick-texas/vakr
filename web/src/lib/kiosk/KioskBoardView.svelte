<!--
@component
One dashboard on a kiosk display: a built-in one by name, or a custom one (UI-013, UI-004).

The single-dashboard pages and the rotation both render through this, so
a dashboard looks and behaves the same in either. Only the dashboard
named is mounted, so only its reads poll (DASH-T06).

Example: `<KioskBoardView board="wan" settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import type { BoardSettings } from './boards.ts';
	import { dashboardIdOf, type KioskPage, type RotationPosition } from '#lib/state/kiosk.ts';
	import KioskDashboard from './KioskDashboard.svelte';
	import KioskHighPriority from './KioskHighPriority.svelte';
	import KioskOverview from './KioskOverview.svelte';
	import KioskRankings from './KioskRankings.svelte';
	import KioskWan from './KioskWan.svelte';

	interface Props {
		board: KioskPage;
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { board, settings, position = null, paused = false, onPause }: Props = $props();
	const custom = $derived(dashboardIdOf(board));
</script>

<div data-kiosk-board={board}>
	{#if custom !== null}
		<KioskDashboard id={custom} {settings} {position} {paused} {onPause} />
	{:else if board === 'overview'}
		<KioskOverview {settings} {position} {paused} {onPause} />
	{:else if board === 'high-priority'}
		<KioskHighPriority {settings} {position} {paused} {onPause} />
	{:else if board === 'wan'}
		<KioskWan {settings} {position} {paused} {onPause} />
	{:else}
		<KioskRankings {settings} {position} {paused} {onPause} />
	{/if}
</div>
