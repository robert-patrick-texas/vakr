<!--
@component
The kiosk chooser: which built-in dashboard this display shows, or a rotation (UI-013).

Set up once, a display is pointed at one of these addresses and left. The
page lists the dashboards, an example rotation, and the URL options with
their bounds (E.31), and lets the display forget its token.
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { forgetDisplay, session } from '#lib/api/session.svelte.ts';
	import { dashboardsQuery } from '#lib/query/reads-dashboards.ts';
	import {
		BOARD_TITLES,
		DWELL_DEFAULT_S,
		DWELL_MAX_S,
		DWELL_MIN_S,
		KIOSK_BOARDS,
		KIOSK_WINDOWS,
		REFRESH_MAX_S,
		REFRESH_MIN_S,
		ROTATE_MAX_PAGES,
		ROTATE_MIN_PAGES
	} from '#lib/state/kiosk.ts';

	const rotation = `/kiosk/rotate?pages=${KIOSK_BOARDS.join(',')}&every=${DWELL_DEFAULT_S}`;
	const custom = createQuery(() => dashboardsQuery());
	let forgetting = $state(false);

	/**
	 * Drop this browser's kiosk cookie; the layout then shows the setup hint.
	 */
	async function forget(): Promise<void> {
		forgetting = true;
		try {
			await forgetDisplay();
		} finally {
			forgetting = false;
		}
	}
</script>

<svelte:head><title>Kiosk · vakr</title></svelte:head>

<h1>Kiosk display</h1>
<p>
	Signed in as <strong>{session.info?.name}</strong> ({session.info?.role}). Choose what this
	display shows; bookmark the address or use it as the browser's start page.
</p>
<ul class="boards">
	{#each KIOSK_BOARDS as board (board)}
		<li><a href="/kiosk/{board}">{BOARD_TITLES[board]}</a></li>
	{/each}
	<li><a href={rotation}>Rotate through all four, {DWELL_DEFAULT_S} s each</a></li>
</ul>
{#if custom.data && custom.data.rows.length > 0}
	<h2>Custom dashboards</h2>
	<ul class="boards" aria-label="Custom dashboards">
		{#each custom.data.rows as b (b.id)}
			<li><a href="/kiosk/dashboard/{b.id}">{b.name}</a></li>
		{/each}
	</ul>
{/if}

<section aria-labelledby="options-h">
	<h2 id="options-h">Address options</h2>
	<dl>
		<dt><code>?refresh=SECONDS</code></dt>
		<dd>
			How often the data is read, {REFRESH_MIN_S} to {REFRESH_MAX_S} s. Without it each dashboard keeps
			its own interval (30 s, rankings 60 s).
		</dd>
		<dt><code>?window=15m</code></dt>
		<dd>The ranking window of the overview and the rankings: {KIOSK_WINDOWS.join(', ')}.</dd>
		<dt><code>/kiosk/rotate?pages=overview,wan&amp;every=60</code></dt>
		<dd>
			Show {ROTATE_MIN_PAGES} to {ROTATE_MAX_PAGES} dashboards in turn (a dashboard may appear more than
			once), each for {DWELL_MIN_S} to {DWELL_MAX_S} s ({DWELL_DEFAULT_S} s by default). A custom dashboard
			joins as <code>dashboard:&lt;uuid&gt;</code>, the ID in its kiosk address.
		</dd>
	</dl>
</section>

{#if session.info?.kind === 'kiosk'}
	<section aria-labelledby="forget-h">
		<h2 id="forget-h">Forget this display</h2>
		<p>
			Drops the kiosk token from this browser only. The token stays valid on other displays until an
			admin revokes it.
		</p>
		<button type="button" onclick={forget} disabled={forgetting}>Forget this display</button>
	</section>
{/if}

<style>
	.boards {
		font-size: 1.3rem;
		line-height: 2;
	}
	dt {
		font-weight: 600;
		margin-top: 0.5rem;
	}
</style>
