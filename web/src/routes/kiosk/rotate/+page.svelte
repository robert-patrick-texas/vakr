<!--
@component
A kiosk rotation: several built-in dashboards in turn, `/kiosk/rotate?pages=overview,wan&every=60`
(UI-013, DASH-T06; Appendix E.31, owner decision 2026-10-21).

Only the dashboard on screen is mounted, so only its reads poll, each at
its own interval (or the kiosk's `refresh`). Five seconds before a switch
the next dashboard's reads are made ahead, so it appears with its data at
once instead of a loading notice; the switch itself replaces one
dashboard with the next in a single update, with no transition, so
nothing slides or flashes (and `prefers-reduced-motion` has nothing to
reduce). The header says which dashboard this is and what comes next.
Rotation counts only time the tab is visible, and a button pauses it.
The address is read once: a rotation is configured, not navigated.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { aheadReads } from '#lib/kiosk/boards.ts';
	import KioskBoardView from '#lib/kiosk/KioskBoardView.svelte';
	import KioskNotice from '#lib/kiosk/KioskNotice.svelte';
	import {
		BOARD_TITLES,
		RotationSchedule,
		kioskOptions,
		rotationOptions,
		type KioskBoard,
		type RotationPosition
	} from '#lib/state/kiosk.ts';

	const TICK_MS = 1000;
	const client = useQueryClient();
	const rotation = rotationOptions(page.url.searchParams);
	const options = kioskOptions(page.url.searchParams);
	const schedule =
		'error' in rotation || 'error' in options
			? null
			: new RotationSchedule(rotation.pages, rotation.dwellMs);
	let board = $state<KioskBoard | null>(schedule?.board ?? null);
	let position = $state<RotationPosition | null>(schedule?.position() ?? null);
	let paused = $state(false);
	let fetchedAhead = false;

	/**
	 * Read the next dashboard's data just before it is shown (the same reads it would make).
	 *
	 * @param next - The dashboard that comes next.
	 */
	function fetchAhead(next: KioskBoard): void {
		if ('error' in options) return;
		for (const read of aheadReads(next, options)) void client.prefetchQuery(read);
	}

	/**
	 * Advance the rotation by the time since the last tick, while visible and not paused.
	 */
	function tick(): void {
		if (schedule === null) return;
		const counting = document.visibilityState === 'visible' && !paused;
		if (schedule.update(Date.now(), counting)) {
			board = schedule.board;
			fetchedAhead = false;
			announce(`Now showing ${BOARD_TITLES[schedule.board]}`);
		} else if (counting && !fetchedAhead && schedule.prefetchDue()) {
			fetchedAhead = true;
			fetchAhead(schedule.next);
		}
		position = schedule.position();
	}

	onMount(() => {
		if (schedule === null) return;
		schedule.update(Date.now(), true);
		const timer = setInterval(tick, TICK_MS);
		return () => clearInterval(timer);
	});
</script>

<svelte:head>
	<title>{board ? BOARD_TITLES[board] : 'Rotation'} · kiosk · vakr</title>
</svelte:head>

{#if 'error' in rotation}
	<KioskNotice state="invalid" title="This rotation address has a mistake">
		<p>{rotation.error}</p>
		<p>Example: <code>/kiosk/rotate?pages=overview,wan,rankings&amp;every=60</code></p>
	</KioskNotice>
{:else if 'error' in options}
	<KioskNotice state="invalid" title="This rotation address has a mistake">
		<p>{options.error}</p>
	</KioskNotice>
{:else if board !== null}
	{#key board}
		<KioskBoardView
			{board}
			settings={options}
			{position}
			{paused}
			onPause={() => {
				paused = !paused;
				announce(paused ? 'Rotation paused' : 'Rotation resumed');
			}}
		/>
	{/key}
{/if}
