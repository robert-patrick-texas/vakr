<!--
@component
One built-in dashboard on a kiosk display: `/kiosk/overview`, `/kiosk/high-priority`,
`/kiosk/wan`, or `/kiosk/rankings` (UI-013).

`?refresh=SECONDS` and `?window=` are read and bounded (E.31); a name or
value outside them takes the whole screen with what is allowed, because a
display is configured once and should not quietly show something else.
-->
<script lang="ts">
	import { page } from '$app/state';
	import KioskBoardView from '#lib/kiosk/KioskBoardView.svelte';
	import KioskNotice from '#lib/kiosk/KioskNotice.svelte';
	import { BOARD_TITLES, KIOSK_BOARDS, isBoard, kioskOptions } from '#lib/state/kiosk.ts';

	const name = $derived(page.params['board'] ?? '');
	const options = $derived(kioskOptions(page.url.searchParams));
</script>

<svelte:head>
	<title>{isBoard(name) ? BOARD_TITLES[name] : 'Kiosk'} · kiosk · vakr</title>
</svelte:head>

{#if !isBoard(name)}
	<KioskNotice state="invalid" title="No such kiosk dashboard">
		<p>
			There is no kiosk dashboard named “{name.slice(0, 40)}”. The dashboards are
			{KIOSK_BOARDS.join(', ')}; <a href="/kiosk">choose one</a>.
		</p>
	</KioskNotice>
{:else if 'error' in options}
	<KioskNotice state="invalid" title="This kiosk address has a mistake">
		<p>{options.error}</p>
	</KioskNotice>
{:else}
	{#key name}
		<KioskBoardView board={name} settings={options} />
	{/key}
{/if}
