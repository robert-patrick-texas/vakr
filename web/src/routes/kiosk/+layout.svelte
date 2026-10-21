<!--
@component
The kiosk display shell: no navigation, large type, links as plain text, and full-screen states
(UI-013, UI-014; Appendix E.31).

It asks the API who the browser is. A kiosk display (or a person signed in
on this browser) sees the kiosk pages; anyone else gets a full-screen
"not signed in" message with the way to set the display up, never the
sign-in page. When any request answers 401, because the kiosk token expired
or was revoked, the data is dropped and the same message takes the whole
screen. When vakr-api cannot be reached, the display says so and asks
again every 15 s. `/kiosk/setup` is shown without any of this, since it is
how a display gets its token. A display whose token ends within 7 days
says so, so an admin can hand over a new one in time.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { describeFailure } from '#lib/api/errors.ts';
	import { loadSession, session } from '#lib/api/session.svelte.ts';
	import { expiresSoon, formatDate } from '#lib/kiosk/expiry.ts';
	import KioskNotice from '#lib/kiosk/KioskNotice.svelte';
	import { setKioskMode } from '#lib/state/kiosk-context.ts';
	import { REFRESH_MIN_S } from '#lib/state/kiosk.ts';

	const { children } = $props();
	setKioskMode();
	let problem = $state<string | null>(null);
	let retry: ReturnType<typeof setTimeout> | undefined;
	const onSetup = $derived(page.url.pathname === '/kiosk/setup');
	const ending = $derived(
		session.info?.kind === 'kiosk' ? expiresSoon(session.info.expires_at ?? null, Date.now()) : null
	);

	/**
	 * Ask who the browser is; while vakr-api does not answer, ask again every 15 s.
	 */
	async function check(): Promise<void> {
		clearTimeout(retry);
		try {
			await loadSession();
			problem = null;
		} catch (error) {
			problem = describeFailure(error).detail;
			retry = setTimeout(() => void check(), REFRESH_MIN_S * 1000);
		}
	}

	onMount(() => {
		void check();
		return () => clearTimeout(retry);
	});
</script>

<div class="kiosk">
	{#if onSetup}
		{@render children()}
	{:else if session.status === 'signed-in'}
		{#if ending}
			<p class="notice notice-amber" role="status" data-state="kiosk-expiring">
				This display's kiosk token expires on {formatDate(ending)}. Ask an admin for a new one
				before then (<code>sudo vakr kiosk create LABEL</code>) and paste it at
				<a href="/kiosk/setup">set up this display</a>.
			</p>
		{/if}
		{@render children()}
	{:else if problem}
		<KioskNotice state="unavailable" title="vakr-api is not answering">
			<p>{problem}</p>
			<p>This display asks again every {REFRESH_MIN_S} seconds.</p>
		</KioskNotice>
	{:else if session.status === 'signed-out'}
		<KioskNotice state="signed-out" title="This display is not signed in">
			<p>
				Its kiosk token was never set up on this browser, has expired, or was revoked. An admin
				creates a new one with <code>sudo vakr kiosk create LABEL</code> or on the Admin page.
			</p>
			<p><a href="/kiosk/setup">Set up this display</a> and paste the new token.</p>
		</KioskNotice>
	{:else}
		<KioskNotice state="loading" title="Starting the display">
			<p>Asking vakr-api who this display is…</p>
		</KioskNotice>
	{/if}
</div>

<style>
	.kiosk {
		font-size: 1.2rem;
	}
	.kiosk :global(h2) {
		font-size: 1.35rem;
	}
	.kiosk :global(table) {
		font-size: 1.05em;
	}
	/* Larger type needs wider cards, or panels scroll sideways on a wall display nobody scrolls. */
	.kiosk :global(.grid) {
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 30rem), 1fr));
	}
	.kiosk :global(.card) {
		overflow-wrap: anywhere;
	}
</style>
