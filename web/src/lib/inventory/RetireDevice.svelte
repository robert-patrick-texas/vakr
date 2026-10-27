<!--
@component
"Retire device" with a confirmation step: the soft delete (META-009; admins only).

Retiring stops polling within seconds; the device's graphs, interfaces,
and identity history stay, and setting the admin state back to Enabled
restores it. The first button only asks; the second, which names the
device, sends `DELETE` with the version the page shows (DB-003). A
refusal (a file manages the device, a peer node, a newer version) is said
in words and announced.

Example: `<RetireDevice record={record} />`
-->
<script lang="ts">
	import { goto } from '$app/navigation';
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import type { DeviceRecordOut } from '#lib/api/types.ts';
	import { deviceHref } from '#lib/links.ts';
	import { refusalOf } from './edit-forms.ts';

	interface Props {
		record: DeviceRecordOut;
	}

	const { record }: Props = $props();
	const client = useQueryClient();
	let asking = $state(false);
	let refused = $state<string | null>(null);

	const retire = createMutation(() => ({
		mutationFn: () =>
			unwrap(
				api.DELETE('/api/v1/devices/{device_uuid}', {
					params: { path: { device_uuid: record.id }, query: { version: record.version } }
				})
			),
		onSuccess: (answer: DeviceRecordOut) => {
			for (const key of ['devices', 'device', 'device-record']) {
				void client.invalidateQueries({ queryKey: [key] });
			}
			announce(`Retired ${answer.name}.`);
			void goto(deviceHref(answer.id));
		},
		onError: (error: unknown) => {
			refused = refusalOf(error, 'device').summary;
			announce(refused);
		}
	}));
</script>

<section class="card" aria-labelledby="retire-h">
	<h2 id="retire-h">Retire</h2>
	{#if record.admin_state === 'retired'}
		<p>This device is retired. Set its admin state to Enabled above to restore it.</p>
	{:else}
		<p>
			Retiring stops polling. The device’s history, interfaces, and identity history stay, and you
			can restore it later.
		</p>
		{#if asking}
			<button type="button" onclick={() => retire.mutate()} disabled={retire.isPending}
				>Yes, retire {record.name}</button
			>
			<button type="button" onclick={() => (asking = false)}>Keep it</button>
		{:else}
			<button type="button" onclick={() => (asking = true)} disabled={!record.editable}
				>Retire device</button
			>
		{/if}
	{/if}
	{#if refused}<p class="notice notice-red" role="alert">{refused}</p>{/if}
</section>
