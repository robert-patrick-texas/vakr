<!--
@component
A link that becomes plain text on a kiosk display (UI-013).

Shared tables and panels use it for device, interface, and filter links,
so the same component serves the interactive pages and the wall display,
where a tap must never navigate away.

Example: `<Link href={deviceHref(id)}>{name}</Link>`
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { inKiosk } from '#lib/state/kiosk-context.ts';

	interface Props {
		href: string;
		children: Snippet;
	}

	const { href, children }: Props = $props();
	const plain = inKiosk();
</script>

{#if plain}<span>{@render children()}</span>{:else}<a {href}>{@render children()}</a>{/if}
