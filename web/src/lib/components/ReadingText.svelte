<!--
@component
One value as the API words it, with why it is missing or old (invariant 1, TIME-006, UI-015).

A `Reading` carries display text, the machine value, a reason when the value
is missing (`—`, never 0), and a stale flag. Missing and stale values are
marked in text, not only in colour, and the reason is read out beside it. In
dense tables (`compact`) the reason is a tooltip and screen-reader text.

Example: `<ReadingText reading={row.loss} compact />`
-->
<script lang="ts">
	import type { Reading } from '#lib/api/types.ts';

	interface Props {
		reading: Reading;
		compact?: boolean;
	}

	const { reading, compact = false }: Props = $props();
	const missing = $derived(reading.value === null);
	const why = $derived(missing && reading.reason ? reading.reason : null);
</script>

<span class="reading" class:missing class:stale={reading.stale} title={compact ? why : null}>
	{reading.text}{#if why}<span class={compact ? 'sr-only' : 'why'}> ({why})</span>{/if}
</span>

<style>
	.missing,
	.stale {
		color: var(--fg-muted);
	}
	.stale {
		font-style: italic;
	}
	.why {
		font-size: 0.85em;
		color: var(--fg-muted);
	}
</style>
