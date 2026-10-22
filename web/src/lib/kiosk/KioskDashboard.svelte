<!--
@component
A custom dashboard on a kiosk display: `/kiosk/dashboard/<uuid>` or `dashboard:<uuid>` in a
rotation (UI-004, UI-013, DASH-T06).

One `/view` request per refresh, at the kiosk's `refresh` if given, else
the dashboard's own interval; graph cards load as they come into view.
The same freshness line, stale note, and signed-out handling as the
built-in kiosk dashboards apply. A dashboard that no longer exists takes
the whole screen with what to do, as an unknown dashboard name does.

Example: `<KioskDashboard id={uuid} settings={{ refreshMs: null, window: '15m' }} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { ApiFailure } from '#lib/api/errors.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import DashboardWidgets from '#lib/dashboards/DashboardWidgets.svelte';
	import { dashboardViewQuery, viewInterval } from '#lib/query/reads-dashboards.ts';
	import type { RotationPosition } from '#lib/state/kiosk.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import type { BoardSettings } from './boards.ts';
	import KioskHeader from './KioskHeader.svelte';
	import KioskNotice from './KioskNotice.svelte';

	interface Props {
		id: string;
		settings: BoardSettings;
		position?: RotationPosition | null;
		paused?: boolean;
		onPause?: () => void;
	}

	const { id, settings, position = null, paused = false, onPause }: Props = $props();
	const query = createQuery(() => dashboardViewQuery(id, settings.refreshMs));
	const gone = $derived(query.error instanceof ApiFailure && query.error.kind === 'not_found');
	const shown = $derived(
		pageState(query, {
			partial: (d) =>
				d.telemetry.value === 'ok'
					? []
					: [`Current values unavailable: ${d.telemetry.reason ?? d.telemetry.text}.`]
		})
	);
</script>

{#if gone}
	<KioskNotice state="invalid" title="This dashboard no longer exists">
		<p>
			The custom dashboard this display shows was deleted. Point the display at another one on
			<a href="/kiosk">the kiosk chooser</a>, or ask an admin to create it again.
		</p>
	</KioskNotice>
{:else}
	<KioskHeader
		title={query.data?.name ?? 'Custom dashboard'}
		updatedAt={query.dataUpdatedAt}
		refreshMs={viewInterval(query.data, settings.refreshMs)}
		{position}
		{paused}
		{onPause}
	/>
	<PageStatus state={shown} label="Dashboard">
		{#snippet children(d)}
			{#if d.widgets.length === 0}<p>This dashboard has no widgets yet.</p>{/if}
			<DashboardWidgets view={d} />
		{/snippet}
	</PageStatus>
{/if}
