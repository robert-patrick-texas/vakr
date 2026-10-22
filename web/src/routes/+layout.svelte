<!--
@component
The app shell: query client, session gate, navigation, the live region, and the main landmark.

On load the shell asks the API who is signed in. A signed-out browser goes
to the sign-in page with the current location kept in `next`; when a
session ends while a page is open (a 401: expired, idle, or revoked), the
cached data is dropped and the sign-in page says why (UI-014). When the API
cannot be reached at all, the shell says the backend is unavailable and
retries on request; it never shows device data it cannot vouch for.

Under `/kiosk` the shell steps aside: no navigation and no sign-in page.
The kiosk layout checks the display's own session and shows its states
full screen (UI-013), so a wall display never lands on a sign-in form.
-->
<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { QueryClientProvider } from '@tanstack/svelte-query';
	import { createQueryClient } from '#lib/query/policy.ts';
	import { loadSession, onSessionEnd, session } from '#lib/api/session.svelte.ts';
	import { describeFailure } from '#lib/api/errors.ts';
	import { announcement } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import AppNav from '#lib/components/AppNav.svelte';

	const { children } = $props();
	const client = createQueryClient();
	let problem = $state<string | null>(null);
	let version = $state<string | null>(null);
	let node = $state<string | null>(null);
	const onLogin = $derived(page.url.pathname === '/login');
	const kiosk = $derived(page.url.pathname === '/kiosk' || page.url.pathname.startsWith('/kiosk/'));
	let checked = false;

	/**
	 * Send a signed-out browser to the sign-in page, keeping where it was.
	 */
	function toLogin(): void {
		if (page.url.pathname === '/login' || kiosk) return;
		const next = `${page.url.pathname}${page.url.search}`;
		void goto(`/login?next=${encodeURIComponent(next)}`, { replace: true });
	}

	/**
	 * Ask who is signed in, then route accordingly.
	 */
	async function check(): Promise<void> {
		problem = null;
		try {
			if ((await loadSession()) === 'signed-out') toLogin();
		} catch (error) {
			problem = describeFailure(error).detail;
		}
	}

	/**
	 * Read the release once for the header; it is never written into the build (VER-001).
	 */
	async function fetchVersion(): Promise<void> {
		try {
			const info = await unwrap(api.GET('/api/v1/version'));
			version = info.version;
			// A position names this node in a two-position pair (MPOS-003).
			node = info.position ? `${info.position} (${info.role})` : null;
		} catch {
			version = null;
			node = null;
		}
	}

	$effect(() => {
		// After any sign-in (page load or the form), read the release once for the header.
		if (session.status === 'signed-in' && version === null) void fetchVersion();
	});

	$effect(() => {
		// Outside the kiosk pages, ask once who is signed in; the kiosk layout asks for itself.
		if (!kiosk && !checked) {
			checked = true;
			void check();
		}
	});

	onMount(() => {
		return onSessionEnd(() => {
			client.clear();
			version = null;
			toLogin();
		});
	});
</script>

<QueryClientProvider {client}>
	<a class="skip" href="#main">Skip to content</a>
	{#if session.status === 'signed-in' && !onLogin && !kiosk}
		<AppNav current={page.url.pathname} {version} {node} />
	{/if}
	<main id="main" tabindex="-1" class:wide={kiosk}>
		{#if kiosk}
			{@render children()}
		{:else if problem}
			<div class="notice notice-red" role="alert" data-state="unavailable">
				<strong>Backend unavailable.</strong>
				{problem}
				<button type="button" onclick={check}>Try again</button>
			</div>
		{:else if session.status === 'unknown'}
			<p class="muted" aria-busy="true" data-state="loading">Checking your session…</p>
		{:else if session.status === 'signed-in' || onLogin}
			{@render children()}
		{:else}
			<p class="muted" data-state="signed-out">Going to the sign-in page…</p>
		{/if}
	</main>
	<div class="sr-only" aria-live="polite" aria-atomic="true" data-testid="announcer">
		{announcement.text}
	</div>
</QueryClientProvider>

<style>
	main {
		padding: 1rem;
		max-width: 110rem;
		margin: 0 auto;
	}
	main:focus {
		outline: none;
	}
	main.wide {
		max-width: none;
	}
	.skip {
		position: absolute;
		left: -10000px;
		top: 0;
	}
	.skip:focus {
		left: 0.5rem;
		top: 0.5rem;
		z-index: 10;
		background: var(--bg);
		padding: 0.3rem 0.6rem;
	}
</style>
