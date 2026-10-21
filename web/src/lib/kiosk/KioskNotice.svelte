<!--
@component
A full-screen kiosk message: not signed in, backend unreachable, or a kiosk URL that is wrong
(UI-013, UI-014).

A wall display has nobody in front of it to read a small banner, so these
states take the whole screen, say what is wrong in words, and say what to
do about it.

Example: `<KioskNotice state="signed-out" title="Not signed in">...</KioskNotice>`
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		/** `data-state` for tests and styling. */
		state: 'signed-out' | 'unavailable' | 'invalid' | 'loading';
		title: string;
		children: Snippet;
	}

	const { state, title, children }: Props = $props();
	const tone: Record<Props['state'], string> = {
		'signed-out': 'amber',
		unavailable: 'red',
		invalid: 'amber',
		loading: 'gray'
	};
</script>

<section
	class="kiosk-notice notice notice-{tone[state]}"
	role={state === 'loading' ? undefined : 'alert'}
	aria-busy={state === 'loading' ? 'true' : undefined}
	data-state="kiosk-{state}"
>
	<h1>{title}</h1>
	{@render children()}
</section>

<style>
	.kiosk-notice {
		min-height: 60vh;
		display: flex;
		flex-direction: column;
		justify-content: center;
		padding: 2rem 3rem;
		font-size: 1.4rem;
	}
	h1 {
		font-size: 2.2rem;
		margin: 0 0 1rem;
	}
</style>
