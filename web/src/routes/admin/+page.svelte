<!--
@component
Admin tasks available in the web UI (12.1 "Admin", admin only).

In this release the admin changes through the API are the identity
review decision and kiosk tokens for wall displays (UI-013). Everything
else an admin does (accounts, inventory, configuration) is on the node
with `vakr`, and the page says where. An operator who opens this address
is told the page is for admins; the API refuses admin changes from
operators anyway.
-->
<script lang="ts">
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import KioskTokens from '#lib/components/KioskTokens.svelte';
</script>

<svelte:head><title>Admin · vakr</title></svelte:head>

<h1>Admin</h1>
{#if !isAdmin()}
	<div class="notice notice-amber" role="alert" data-state="denied">
		<strong>Permission denied.</strong> This page is for admins. Your role can read every monitoring page.
	</div>
{:else}
	<ul>
		<li>
			<a href="/identity-reviews">Identity reviews</a>: map a renumbered interface to its history,
			or give it a new identity.
		</li>
	</ul>
	<p class="muted">
		Accounts (<code>vakr user</code>), inventory (<code>/etc/vakr/inventory.toml</code> and
		<code>vakr inventory apply</code>), and configuration are managed on the node; rediscovery
		requests, metadata editing, and your own dashboards are not in this release.
	</p>
	<KioskTokens />
{/if}
