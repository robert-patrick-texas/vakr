<!--
@component
The add/edit device form (27.4, META-008, DB-003; admins only).

The fields are those of a `[[devices]]` table in `inventory.toml`, and the
API applies the file's rules to them; every problem it names is shown
beside its field. A new device is API-managed. A device the file manages
is shown read-only, each field saying which file manages it (META-008).
A change carries the version the form opened: when someone else saved in
between, the form says so and offers to reload instead of overwriting.
On a peer node the API refuses and the message names the primary
(MPOS-003). Writes carry the CSRF token and are never retried.

Example: `<DeviceForm record={null} />` (add) or `<DeviceForm {record} onReload={refetch} />`
-->
<script module lang="ts">
	import type { DeviceField as Field } from './edit-forms.ts';

	const LABELS: Record<Field, string> = {
		name: 'Name',
		address: 'Poll address',
		site: 'Site',
		credential: 'SNMP credential',
		tags: 'Tags',
		groups: 'Groups',
		admin_state: 'Admin state',
		interval: 'Poll interval',
		discovery_interval: 'Rediscovery interval',
		reachability: 'Reachability',
		address_family: 'Address family',
		comment: 'Comment'
	};
	const CHOICES: Partial<Record<Field, readonly (readonly [string, string])[]>> = {
		admin_state: [
			['enabled', 'Enabled'],
			['maintenance', 'Maintenance'],
			['disabled', 'Disabled']
		],
		reachability: [
			['', 'Default'],
			['icmp_gated', 'ICMP gated'],
			['snmp_fallback', 'SNMP fallback'],
			['icmp_only', 'ICMP only'],
			['snmp_only', 'SNMP only']
		],
		address_family: [
			['', 'Either'],
			['ipv4', 'IPv4'],
			['ipv6', 'IPv6']
		]
	};
</script>

<script lang="ts">
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { tick } from 'svelte';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import type { DeviceRecordOut } from '#lib/api/types.ts';
	import { deviceHref } from '#lib/links.ts';
	import { filterOptions } from '#lib/query/options.svelte.ts';
	import {
		DEVICE_FIELDS,
		deviceDraft,
		emptyDevice,
		refusalOf,
		toDeviceCreate,
		toDevicePatch,
		type DeviceField,
		type Refusal
	} from './edit-forms.ts';
	import FormField from './FormField.svelte';

	interface Props {
		/** The stored device, or null to add one. */
		record: DeviceRecordOut | null;
		/** Read the stored device again after a conflict. */
		onReload?: () => void;
		/** The stored record after a save; the page shows it and opens the new version. */
		onSaved: (answer: DeviceRecordOut) => void;
	}

	const { record, onReload, onSaved }: Props = $props();
	// The page re-creates the form for each version it opens (`{#key}`), so these are fixed.
	// svelte-ignore state_referenced_locally
	const current = record;
	const before = current ? deviceDraft(current) : emptyDevice();
	const draft = $state({ ...before });
	let refusal = $state<Refusal | null>(null);
	let summary: HTMLElement | null = $state(null);
	const options = filterOptions();
	const client = useQueryClient();
	const editable = current?.editable ?? true;
	const owner = current && !current.editable ? current.managed_by_source : null;
	const NOTES: Record<DeviceField, string> = {
		name: 'Unique, retired devices included.',
		address: 'IP address or DNS name to poll; not one another device polls.',
		site: 'Sites come from inventory.toml.',
		credential: 'The SNMP credential’s name in the credentials file, never the community.',
		tags: 'Comma-separated; missing tags are created.',
		groups: 'Comma-separated; missing groups are created.',
		admin_state: 'Retire a device with “Retire device”.',
		interval: 'Poll interval override, such as 120s (30s to 1h); blank for the default.',
		discovery_interval: 'Rediscovery override, such as 24h (30s to 30d).',
		reachability: 'How ICMP gates SNMP; blank for the default.',
		address_family: 'For a DNS name with both families.',
		comment: 'Up to 4,096 characters.'
	};

	/**
	 * The problem or ownership note of a field.
	 *
	 * @param field - The field.
	 * @returns The message, or `null`.
	 */
	function problem(field: DeviceField): string | null {
		return refusal?.fields[field] ?? (owner ? `Managed by ${owner}.` : null);
	}

	const save = createMutation(() => ({
		mutationFn: async () => {
			if (current === null) {
				return unwrap(api.POST('/api/v1/devices', { body: toDeviceCreate(draft) }));
			}
			const path = { params: { path: { device_uuid: current.id } } };
			const body = toDevicePatch(draft, before, current.version);
			return unwrap(api.PATCH('/api/v1/devices/{device_uuid}', { ...path, body }));
		},
		onSuccess: (answer: DeviceRecordOut) => {
			refusal = null;
			client.setQueryData(['device-record', answer.id], answer);
			for (const key of ['devices', 'device', 'device-interfaces']) {
				void client.invalidateQueries({ queryKey: [key] });
			}
			announce(answer.changes.length ? `Saved ${answer.name}.` : 'Nothing to change.');
			onSaved(answer);
		},
		onError: async (error: unknown) => {
			refusal = refusalOf(error, 'device');
			announce(refusal.summary);
			await tick();
			summary?.focus();
		}
	}));
</script>

<form
	class="inventory-form"
	aria-label={current ? `Edit ${current.name}` : 'Add a device'}
	onsubmit={(e) => {
		e.preventDefault();
		save.mutate();
	}}
>
	{#if owner}
		<p class="notice notice-amber" data-testid="managed-by-file">
			Managed by {owner}: change it there and run <code>vakr inventory apply</code> (META-008).
		</p>
	{/if}
	<fieldset disabled={!editable}>
		<legend class="sr-only">Device fields</legend>
		<div class="grid">
			{#each DEVICE_FIELDS as field (field)}
				{@const choices = CHOICES[field]}
				<FormField
					id="dev-{field}"
					label={LABELS[field]}
					note={NOTES[field]}
					problem={problem(field)}
				>
					{#if field === 'comment'}
						<textarea
							id="dev-comment"
							rows="2"
							maxlength="4096"
							aria-describedby="dev-comment-note"
							bind:value={draft.comment}></textarea>
					{:else if field === 'site' || choices}
						<select
							id="dev-{field}"
							aria-describedby="dev-{field}-note"
							aria-invalid={refusal?.fields[field] ? 'true' : undefined}
							bind:value={draft[field]}
						>
							{#if field === 'site'}
								<option value="">No site</option>
								{#each options.value.sites as s (s.id)}<option value={s.name}>{s.name}</option
									>{/each}
							{/if}
							{#each choices ?? [] as [value, text] (value)}<option {value}>{text}</option>{/each}
							{#if field === 'admin_state' && before.admin_state === 'retired'}
								<option value="retired">Retired</option>
							{/if}
						</select>
					{:else}
						<input
							id="dev-{field}"
							required={field === 'name' || field === 'address'}
							maxlength={field === 'tags' || field === 'groups' ? 4096 : 255}
							autocomplete="off"
							aria-describedby="dev-{field}-note"
							aria-invalid={refusal?.fields[field] ? 'true' : undefined}
							bind:value={draft[field]}
						/>
					{/if}
				</FormField>
			{/each}
		</div>
	</fieldset>
	{#if refusal}
		<div class="notice notice-red" role="alert" tabindex="-1" bind:this={summary}>
			<strong>Not saved.</strong>
			{refusal.summary}
			{#if refusal.conflict && onReload}
				<button type="button" onclick={onReload}>Reload the device</button>
			{/if}
		</div>
	{/if}
	<div class="row">
		{#if editable}<button type="submit" disabled={save.isPending}
				>{current ? 'Save changes' : 'Add device'}</button
			>{/if}
		<a href={current ? deviceHref(current.id) : '/devices'}
			>{current ? 'Back to the device' : 'Cancel'}</a
		>
	</div>
</form>

<style>
	.inventory-form fieldset {
		border: none;
		margin: 0;
		padding: 0;
	}
	.grid {
		display: grid;
		grid-template-columns: max-content minmax(12rem, 36rem);
		gap: 0.6rem 0.8rem;
		align-items: start;
		margin-bottom: var(--gap);
	}
</style>
