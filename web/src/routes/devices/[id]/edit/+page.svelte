<!--
@component
Edit a device (27.4, META-008, META-009, DB-003; admins only).

The form opens on the stored record and its version. A device
`inventory.toml` manages is shown read-only, naming the file. After a
conflict, Reload reads the newer version and opens the form on it.
Retiring, the soft delete, asks for confirmation first.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import DeviceForm from '#lib/inventory/DeviceForm.svelte';
	import RetireDevice from '#lib/inventory/RetireDevice.svelte';
	import SavedNotice from '#lib/inventory/SavedNotice.svelte';
	import type { DeviceRecordOut } from '#lib/api/types.ts';
	import { deviceHref } from '#lib/links.ts';
	import { deviceRecordQuery } from '#lib/query/reads-inventory.ts';
	import { pageState } from '#lib/state/page-state.ts';

	const id = $derived(page.params['id'] ?? '');
	const record = createQuery(() => deviceRecordQuery(id));
	const shown = $derived(pageState(record));
	let saved = $state<DeviceRecordOut | null>(null);
</script>

<svelte:head><title>Edit {record.data?.name ?? 'device'} · vakr</title></svelte:head>

<p><a href={deviceHref(id)}>← Back to the device</a></p>
{#if !isAdmin()}
	<p class="notice notice-amber">Only an admin can change devices.</p>
{:else}
	<PageStatus state={shown} label="Device">
		{#snippet children(d)}
			<h1>Edit {d.name}</h1>
			{#if saved}<SavedNotice record={saved} created={false} />{/if}
			{#key d.version}
				<DeviceForm
					record={d}
					onReload={() => void record.refetch()}
					onSaved={(answer) => (saved = answer)}
				/>
				<RetireDevice record={d} />
			{/key}
		{/snippet}
	</PageStatus>
{/if}
