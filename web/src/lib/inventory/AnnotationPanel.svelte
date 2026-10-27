<!--
@component
An interface's classification on its page: what it is for, its vakr tags and description tags,
and, for admins, the annotation form (META-007, META-011, META-013).

Everyone sees the interest class, the flags, both kinds of tag, who
manages the annotation, and whether the interface is still "not yet
classified" (said in words). Admins also get the form; after a save it
opens again on the saved version, and the outcome stays on the page.

Example: `<AnnotationPanel interfaceId={id} />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import type { AnnotationOut } from '#lib/api/types.ts';
	import { annotationQuery } from '#lib/query/reads-inventory.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import AnnotationForm from './AnnotationForm.svelte';

	interface Props {
		interfaceId: string;
	}

	const { interfaceId }: Props = $props();
	const query = createQuery(() => annotationQuery(interfaceId));
	const shown = $derived(pageState(query));
	let saved = $state<AnnotationOut | null>(null);
</script>

<section class="card" aria-labelledby="class-h">
	<h2 id="class-h">Classification</h2>
	<PageStatus state={shown} label="Classification">
		{#snippet children(a)}
			<dl class="facts" data-testid="classification">
				<dt>Interest</dt>
				<dd>{a.interest === 'primary' ? 'High priority (primary)' : 'Other (secondary)'}</dd>
				<dt>Flags</dt>
				<dd>
					{[a.sticky && 'Sticky', a.wan_circuit && 'WAN circuit'].filter(Boolean).join(', ') ||
						'None'}
				</dd>
				<dt>vakr tags</dt>
				<dd>{a.tags.join(', ') || 'None'}</dd>
				<dt>Description tags</dt>
				<dd>{a.description_tags.join(', ') || 'None'}</dd>
				<dt>Classified</dt>
				<dd>{a.classified ? 'Yes' : 'Not yet classified'}</dd>
				<dt>Managed by</dt>
				<dd>{a.editable ? 'vakr (API-managed)' : a.managed_by_source}</dd>
			</dl>
			{#if isAdmin()}
				{#if saved}
					<p class="notice notice-gray" role="status" data-testid="annotation-saved">
						{saved.changes.length ? `Saved: ${saved.changes.join('; ')}.` : 'Nothing to change.'}
					</p>
				{/if}
				{#key a.version}
					<AnnotationForm
						annotation={a}
						onSaved={(answer) => (saved = answer)}
						onReload={() => void query.refetch()}
					/>
				{/key}
			{/if}
		{/snippet}
	</PageStatus>
</section>
