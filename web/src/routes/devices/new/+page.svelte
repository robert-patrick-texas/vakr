<!--
@component
Add a device by hand (27.4; admins only).

The device is API-managed; collectors discover it within a minute.
Operators are told who can add devices; the API refuses them anyway.
-->
<script lang="ts">
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import type { DeviceRecordOut } from '#lib/api/types.ts';
	import DeviceForm from '#lib/inventory/DeviceForm.svelte';
	import SavedNotice from '#lib/inventory/SavedNotice.svelte';

	let added = $state<DeviceRecordOut | null>(null);
</script>

<svelte:head><title>Add a device · vakr</title></svelte:head>

<p><a href="/devices">← Devices</a></p>
<h1>Add a device</h1>
{#if added}
	<SavedNotice record={added} created={true} />
	<p><button type="button" onclick={() => (added = null)}>Add another device</button></p>
{:else if isAdmin()}
	<p class="muted">
		Devices you add here are managed in vakr, not in <code>inventory.toml</code>. Collectors
		discover a new device within a minute and then poll it.
	</p>
	<DeviceForm record={null} onSaved={(answer) => (added = answer)} />
{:else}
	<p class="notice notice-amber">Only an admin can add devices.</p>
{/if}
