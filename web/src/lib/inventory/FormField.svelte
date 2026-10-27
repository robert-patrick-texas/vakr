<!--
@component
One labelled field of an inventory form, with its note or its problem beside it (UI-011).

The input is the child; give it `id={id}` and `aria-describedby="{id}-note"`,
and `aria-invalid` when `problem` is set. The note always exists, so the
reference is never dangling. A problem says "Problem:" in words, not only
in colour (WEB-T09), and a field a file manages says which file.

Example:
```svelte
<FormField id="dev-name" label="Name" note="Unique" problem={errors.name}>
  <input id="dev-name" aria-describedby="dev-name-note" bind:value={draft.name} />
</FormField>
```
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		id: string;
		label: string;
		/** What the field takes, shown while there is no problem. */
		note: string;
		/** The API's or the form's message for this field. */
		problem?: string | null;
		children: Snippet;
	}

	const { id, label, note, problem = null, children }: Props = $props();
</script>

<label for={id}>{label}</label>
<div class="field">
	{@render children()}
	<p id="{id}-note" class="note" class:problem={problem !== null}>
		{#if problem !== null}<strong>Problem:</strong> {problem}{:else}{note}{/if}
	</p>
</div>

<style>
	.field {
		display: grid;
		gap: 0.15rem;
	}
	.note {
		margin: 0;
		font-size: 0.85rem;
		color: var(--fg-muted);
	}
	.problem {
		color: var(--fg);
		border-left: 3px solid var(--red-fg);
		padding-left: 0.4rem;
	}
</style>
