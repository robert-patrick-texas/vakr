<!--
@component
A kiosk dashboard's heading, how fresh its data is, and where a rotation stands (UI-013, UI-015).

"Data as of 14:05:09 · refreshes every 30 s" says when the answer on
screen arrived, so a wall display never implies it is current when it is
not; a failed refresh adds the page's own stale note below. In a
rotation, "2 of 3 · next: WAN circuits in 40 s" and a pause button follow.
None of this is a live region: it changes every second, and screen
readers hear only the switch to another dashboard.

Example: `<KioskHeader title="WAN circuits" updatedAt={query.dataUpdatedAt} refreshMs={30000} />`
-->
<script lang="ts">
	import { fetchedAt } from '#lib/state/page-state.ts';
	import type { RotationPosition } from '#lib/state/kiosk.ts';

	interface Props {
		/** The dashboard's title: a built-in one's, or a custom dashboard's name. */
		title: string;
		/** When the shown answer arrived (ms since the epoch); 0 before the first one. */
		updatedAt: number;
		/** The dashboard's refresh interval. */
		refreshMs: number;
		/** The rotation's position, when the dashboard is part of one. */
		position?: RotationPosition | null;
		/** Whether the rotation is paused. */
		paused?: boolean;
		/** Pause or resume the rotation. */
		onPause?: () => void;
	}

	const { title, updatedAt, refreshMs, position = null, paused = false, onPause }: Props = $props();
	const every = $derived(Math.round(refreshMs / 1000));
</script>

<header class="kiosk-head">
	<h1>{title}</h1>
	<p class="fresh" data-testid="kiosk-freshness">
		{updatedAt > 0 ? `Data as of ${fetchedAt(updatedAt)}` : 'Waiting for the first answer'}
		· refreshes every {every} s
	</p>
	{#if position}
		<p class="position" data-testid="kiosk-position">
			{position.current} of {position.total} · next: {position.nextTitle}
			{paused ? '(paused)' : `in ${position.nextInS} s`}
			{#if onPause}
				<button type="button" onclick={onPause} aria-pressed={paused}
					>{paused ? 'Resume rotation' : 'Pause rotation'}</button
				>
			{/if}
		</p>
	{/if}
</header>

<style>
	.kiosk-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.3rem 1.5rem;
		margin-bottom: 0.6rem;
	}
	h1 {
		margin: 0;
	}
	.fresh,
	.position {
		margin: 0;
		color: var(--fg-muted);
	}
	.position {
		margin-left: auto;
	}
</style>
