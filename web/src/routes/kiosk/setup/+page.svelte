<!--
@component
Set up a kiosk display: paste the kiosk token once (UI-013, E.30 #17).

The token goes once, in the body of `POST /api/v1/auth/kiosk`, and the API
answers with an `HttpOnly` kiosk cookie; page script never keeps it. There
is deliberately no way to pass the token in the address, so it never
lands in history, bookmarks, or a synced browser profile. The field is a
password field, so a token pasted on a wall display is not shown to the
room. On success the display goes to the kiosk chooser.
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiFailure } from '#lib/api/errors.ts';
	import { setUpDisplay } from '#lib/api/session.svelte.ts';
	import { announce } from '#lib/a11y/announce.svelte.ts';

	let token = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);

	/**
	 * Say why the API refused the token.
	 *
	 * @param error - What the request threw.
	 * @returns One sentence.
	 */
	function explain(error: unknown): string {
		if (!(error instanceof ApiFailure)) return 'Setting up the display failed.';
		if (error.kind === 'unauthenticated') {
			return 'This is not a live kiosk token: it is mistyped, expired, or revoked. Ask an admin for a new one.';
		}
		if (error.kind === 'forbidden') return `Refused: ${error.message}.`;
		if (error.kind === 'throttled')
			return 'Too many displays are being set up at once; try again shortly.';
		return `vakr-api did not accept the token: ${error.message}.`;
	}

	/**
	 * Send the token once; on success go to the chooser.
	 *
	 * @param event - The form submission.
	 */
	async function submit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		busy = true;
		problem = null;
		try {
			await setUpDisplay(token.trim());
			token = '';
			announce('This display is set up.');
			await goto('/kiosk', { replace: true });
		} catch (error) {
			problem = explain(error);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Set up this display · vakr</title></svelte:head>

<h1>Set up this display</h1>
<p>
	Paste the kiosk token an admin created with <code>sudo vakr kiosk create LABEL</code> or on the Admin
	page. It is sent once to vakr-api, which keeps this browser signed in as a read-only display until the
	token expires or is revoked.
</p>
<form onsubmit={submit} class="setup">
	<label for="kiosk-token">Kiosk token</label>
	<input
		id="kiosk-token"
		type="password"
		autocomplete="off"
		spellcheck="false"
		maxlength="128"
		required
		bind:value={token}
	/>
	<button type="submit" disabled={busy || token.trim() === ''}>Set up</button>
</form>
{#if problem}
	<p class="notice notice-amber" role="alert" data-state="kiosk-refused">{problem}</p>
{/if}

<style>
	.setup {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.6rem;
		margin: 1rem 0;
	}
	.setup input {
		min-width: 28rem;
		font-family: var(--mono);
	}
</style>
