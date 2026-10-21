<!--
@component
A status shown with colour, icon, and text together (12.8, UI-011, WEB-T09).

The colour follows 12.8: red is confirmed attention, amber degraded or
unknown, green fresh and healthy, gray administratively offline. The icon
is the API's shape for the meaning (✖, ▲, ?, ✔, ○, ◐), so the state still
reads in grayscale or for a colour-blind operator, and the label is the
accessible name: the icon is hidden from screen readers.

Example: `<StatusBadge color="red" icon="✖" label="Down" />`
-->
<script lang="ts">
	import type { StatusColor } from '#lib/api/types.ts';

	interface Props {
		/** 12.8 colour; never shown without the icon and label. */
		color: StatusColor;
		/** The API's icon glyph for this state. */
		icon: string;
		/** Operator text such as `Down` or `Unknown — collector degraded`. */
		label: string;
		/** Smaller, for dense tables and cards. */
		small?: boolean;
	}

	const { color, icon, label, small = false }: Props = $props();
</script>

<span class="status status-{color}" class:small data-status={color}>
	<span class="icon" aria-hidden="true">{icon}</span>
	<span class="label">{label}</span>
</span>

<style>
	.status {
		display: inline-flex;
		align-items: center;
		gap: 0.35em;
		border: 1px solid currentColor;
		border-radius: 999px;
		padding: 0.05rem 0.55rem;
		font-weight: 600;
		white-space: nowrap;
	}
	.small {
		font-size: 0.85em;
		padding: 0 0.45rem;
	}
	.icon {
		font-family: var(--font);
		line-height: 1;
	}
	.status-red {
		color: var(--red-fg);
		background: var(--red-bg);
	}
	.status-amber {
		color: var(--amber-fg);
		background: var(--amber-bg);
	}
	.status-green {
		color: var(--green-fg);
		background: var(--green-bg);
	}
	.status-gray {
		color: var(--gray-fg);
		background: var(--gray-bg);
		border-style: dashed;
	}
</style>
