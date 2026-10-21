<!--
@component
Sign-in (SEC-010, AUTH-002, AUTH-004; UI-014 for an ended session).

The form posts JSON to `POST /api/v1/auth/login`; the API sets the
`HttpOnly` session cookie, so the password and cookie are never kept by
the page. A refused sign-in says only that it failed (the API answers the
same for every reason); throttling says when to try again; plain HTTP from
another host is refused by the API and explained. After sign-in the user
returns to the page they were on (`next`, same origin only).
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiFailure } from '#lib/api/errors.ts';
	import { session, signIn } from '#lib/api/session.svelte.ts';
	import { safeNext } from '#lib/links.ts';

	let username = $state('');
	let password = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);
	const next = $derived(safeNext(page.url.searchParams.get('next')));

	$effect(() => {
		if (session.status === 'signed-in' && !busy) void goto(next, { replace: true });
	});

	/**
	 * Explain a refused sign-in.
	 *
	 * @param failure - What the API answered.
	 * @returns One sentence.
	 */
	function explain(failure: unknown): string {
		if (!(failure instanceof ApiFailure)) return 'Sign-in failed.';
		if (failure.kind === 'unauthenticated')
			return 'Sign-in failed: check the username and password.';
		if (failure.kind === 'throttled') {
			const wait = failure.retryAfter === null ? 'a moment' : `${failure.retryAfter} s`;
			return `Too many failed sign-ins: wait ${wait} and try again.`;
		}
		if (failure.code === 'insecure_transport') {
			return 'Sign-in needs HTTPS: open vakr through its HTTPS address.';
		}
		if (failure.kind === 'unavailable') return `vakr-api is unavailable: ${failure.message}.`;
		return `Sign-in refused: ${failure.message}.`;
	}

	/**
	 * Submit the form.
	 *
	 * @param event - The submission.
	 */
	async function submit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		busy = true;
		error = null;
		try {
			await signIn(username, password);
			password = '';
			await goto(next, { replace: true });
		} catch (failure) {
			error = explain(failure);
			password = '';
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Sign in · vakr</title></svelte:head>

<section class="login card">
	<h1>Sign in to vakr</h1>
	{#if session.ended === 'expired'}
		<p class="notice notice-amber" role="status" data-state="session-ended">
			Your session ended (it expired, was idle too long, or was signed out elsewhere). Sign in again
			to continue where you were.
		</p>
	{:else if session.ended === 'signed-out'}
		<p class="notice notice-gray" role="status">You are signed out.</p>
	{/if}
	<form onsubmit={submit}>
		<label for="username">Username</label>
		<input
			id="username"
			name="username"
			autocomplete="username"
			required
			maxlength="256"
			bind:value={username}
		/>
		<label for="password">Password</label>
		<input
			id="password"
			name="password"
			type="password"
			autocomplete="current-password"
			required
			maxlength="1024"
			bind:value={password}
		/>
		{#if error}<p class="notice notice-red" role="alert">{error}</p>{/if}
		<button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
	</form>
</section>

<style>
	.login {
		max-width: 26rem;
		margin: 3rem auto;
	}
	form {
		display: grid;
		gap: 0.4rem;
	}
	button {
		margin-top: 0.6rem;
		justify-self: start;
	}
</style>
