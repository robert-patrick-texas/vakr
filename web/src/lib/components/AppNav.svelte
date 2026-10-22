<!--
@component
Global navigation, global search, and the signed-in user (12.1).

The items are those of 12.1: Overview, Devices, Interfaces, Sites,
Dashboards, Collectors/Health, Search, and Admin for admins only (the API
still checks every admin request; hiding the link is not the control). The
current page is marked with `aria-current="page"`. The search box submits
to the search page; any interface's full history is two interactions away
(search, then the hit), UI-017.

Example: `<AppNav current="/devices" />`
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { session, isAdmin, signOut } from '#lib/api/session.svelte.ts';

	interface Props {
		/** The current path, for `aria-current`. */
		current: string;
		/** The release, from `GET /api/v1/version`, or null until known. */
		version: string | null;
		/** This node's position and role, such as `west (peer)`, or null for a single node. */
		node?: string | null;
	}

	const { current, version, node = null }: Props = $props();
	let text = $state('');
	let signingOut = $state(false);

	const items = $derived([
		{ href: '/', label: 'Overview' },
		{ href: '/devices', label: 'Devices' },
		{ href: '/interfaces', label: 'Interfaces' },
		{ href: '/sites', label: 'Sites' },
		{ href: '/dashboards', label: 'Dashboards' },
		{ href: '/collectors', label: 'Collectors/Health' },
		{ href: '/search', label: 'Search' },
		...(isAdmin() ? [{ href: '/admin', label: 'Admin' }] : [])
	]);

	/**
	 * Tell whether a nav item is the current section.
	 *
	 * @param href - The item's path.
	 * @returns True for the current page or one below it.
	 */
	function isCurrent(href: string): boolean {
		return href === '/' ? current === '/' : current === href || current.startsWith(`${href}/`);
	}

	/**
	 * Go to the search page with the box's text.
	 *
	 * @param event - The form submission.
	 */
	function search(event: SubmitEvent): void {
		event.preventDefault();
		const q = text.trim();
		if (q) void goto(`/search?q=${encodeURIComponent(q)}`);
	}

	/**
	 * Sign out; the layout returns to the sign-in page.
	 */
	async function leave(): Promise<void> {
		signingOut = true;
		try {
			await signOut();
		} finally {
			signingOut = false;
		}
	}
</script>

<header class="bar">
	<a class="brand" href="/">vakr</a>
	<nav aria-label="Main">
		<ul>
			{#each items as item (item.href)}
				<li>
					<a href={item.href} aria-current={isCurrent(item.href) ? 'page' : undefined}
						>{item.label}</a
					>
				</li>
			{/each}
		</ul>
	</nav>
	<form role="search" class="search" onsubmit={search}>
		<label for="global-search" class="sr-only">Search devices, interfaces, sites, circuits</label>
		<input
			id="global-search"
			type="search"
			maxlength="256"
			placeholder="Search name, address, circuit…"
			bind:value={text}
		/>
		<button type="submit">Search</button>
	</form>
	<div class="who">
		{#if session.info}
			<span>{session.info.name} <span class="muted">({session.info.role})</span></span>
			{#if session.info.kind === 'user'}
				<button type="button" onclick={leave} disabled={signingOut}>Sign out</button>
			{/if}
		{/if}
		{#if node}<span class="muted version">{node}</span>{/if}
		{#if version}<span class="muted version">vakr {version}</span>{/if}
	</div>
</header>

<style>
	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.5rem 1rem;
		border-bottom: 1px solid var(--border);
		background: var(--bg-raised);
	}
	.brand {
		font-weight: 700;
		font-size: 1.15rem;
		text-decoration: none;
		color: var(--fg);
	}
	nav ul {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.9rem;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	nav a {
		text-decoration: none;
		padding: 0.15rem 0.1rem;
	}
	nav a[aria-current='page'] {
		font-weight: 700;
		border-bottom: 3px solid var(--link);
	}
	.search {
		display: flex;
		gap: 0.3rem;
		margin-left: auto;
	}
	.who {
		display: flex;
		gap: 0.6rem;
		align-items: center;
	}
	.version {
		font-size: 0.85em;
	}
</style>
