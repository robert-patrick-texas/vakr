<!--
@component
A custom dashboard on a kiosk display: `/kiosk/dashboard/<uuid>` (UI-004, UI-013).

`?refresh=SECONDS` overrides the dashboard's own interval within the E.31
bounds. An address that is not a dashboard UUID, or a value out of
bounds, takes the whole screen with what is allowed.
-->
<script lang="ts">
	import { page } from '$app/state';
	import KioskBoardView from '#lib/kiosk/KioskBoardView.svelte';
	import KioskNotice from '#lib/kiosk/KioskNotice.svelte';
	import {
		DASHBOARD_PREFIX,
		dashboardIdOf,
		kioskOptions,
		type KioskPage
	} from '#lib/state/kiosk.ts';

	const raw = $derived(page.params['id'] ?? '');
	const id = $derived(dashboardIdOf(`${DASHBOARD_PREFIX}${raw}`));
	const options = $derived(kioskOptions(page.url.searchParams));
	const board = $derived<KioskPage | null>(id === null ? null : `${DASHBOARD_PREFIX}${id}`);
</script>

<svelte:head><title>Dashboard · kiosk · vakr</title></svelte:head>

{#if board === null}
	<KioskNotice state="invalid" title="This kiosk address has a mistake">
		<p>
			“{raw.slice(0, 40)}” is not a dashboard ID. Open the dashboard in vakr and use the kiosk
			address it shows, or <a href="/kiosk">choose a dashboard</a>.
		</p>
	</KioskNotice>
{:else if 'error' in options}
	<KioskNotice state="invalid" title="This kiosk address has a mistake">
		<p>{options.error}</p>
	</KioskNotice>
{:else}
	{#key board}
		<KioskBoardView {board} settings={options} />
	{/key}
{/if}
