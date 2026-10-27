<!--
@component
What the last device save did: the changes or "nothing to change", any warnings (such as no
SNMP credential), and a link to the device page (27.4).

It lives in the page, outside the form, so it stays when the page opens the
form again on the saved version.

Example: `<SavedNotice record={saved} created={false} />`
-->
<script lang="ts">
	import type { DeviceRecordOut } from '#lib/api/types.ts';
	import { deviceHref } from '#lib/links.ts';

	interface Props {
		record: DeviceRecordOut;
		/** True right after the device was added. */
		created: boolean;
	}

	const { record, created }: Props = $props();
	const said = $derived(
		created
			? `Added ${record.name}. Collectors discover it within a minute.`
			: record.changes.length
				? `Saved ${record.name} (version ${record.version}): ${record.changes.join('; ')}.`
				: 'Nothing to change.'
	);
</script>

<div class="notice notice-gray" role="status" data-testid="device-saved">
	<p>{said}</p>
	{#each record.warnings as w (w.field)}<p><strong>Warning:</strong> {w.message}</p>{/each}
	<p><a href={deviceHref(record.id)}>Open the device page</a></p>
</div>
