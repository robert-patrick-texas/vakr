<!--
@component
Open interface identity reviews, oldest first (IFID-007, 12.4 "discovery/identity review").

When discovery cannot tell which existing interface a row is, it opens a
review. Everyone can read them; an admin answers each one here (map to a
candidate, or a new identity). Operators see the reviews without the
decision controls.
-->
<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import { formatInstant } from '#lib/format/time.ts';
	import { deviceHref, interfaceHref } from '#lib/links.ts';
	import { reviewsQuery } from '#lib/query/reads-catalog.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import ReviewDecision from '#lib/components/ReviewDecision.svelte';

	const UUID = /^[0-9a-f-]{36}$/i;
	const device = $derived.by(() => {
		const d = page.url.searchParams.get('device');
		return d && UUID.test(d) ? d : null;
	});
	const trail = new CursorTrail();
	const query = createQuery(() => reviewsQuery(device, trail.cursor));
	const shown = $derived(pageState(query, { isEmpty: (d) => d.items.length === 0 }));
</script>

<svelte:head><title>Identity reviews · vakr</title></svelte:head>

<h1>Identity reviews</h1>
{#if device}<p><a href="/identity-reviews">Show reviews of every device</a></p>{/if}
{#if !isAdmin()}
	<p class="notice notice-gray">You can read these reviews; an admin decides them.</p>
{/if}
<PageStatus state={shown} label="Identity reviews" emptyText="No identity review is open.">
	{#snippet children(d)}
		{#each d.items as r (r.id)}
			<article class="card review" data-review={r.id}>
				<h2>
					<a href={deviceHref(r.device_id)}>{r.device}</a>: ifIndex {r.if_index}
					<span class="muted">{r.if_name} {r.if_descr !== r.if_name ? `(${r.if_descr})` : ''}</span>
				</h2>
				<p>
					Opened {formatInstant(r.opened_at, 'UTC')} UTC · outcome so far: {r.outcome}{#if r.if_alias}
						· alias “{r.if_alias}”{/if}
				</p>
				{#if r.evidence.length > 0}<p class="muted">Evidence: {r.evidence.join('; ')}</p>{/if}
				<p>
					Candidates:
					{#each r.candidates as c, i (c.interface_id)}{#if i > 0},
						{/if}<a href={interfaceHref(c.interface_id)}>{c.name}</a>
						<span class="muted">(ifIndex {c.if_index ?? '—'}, {c.lifecycle})</span
						>{:else}none{/each}
				</p>
				{#if isAdmin()}<ReviewDecision review={r} />{/if}
			</article>
		{/each}
		<Pager
			page={d.page}
			shown={d.items.length}
			pageNumber={trail.number}
			noun="reviews"
			onNext={trail.next}
			onPrevious={trail.previous}
			onFirst={trail.reset}
		/>
	{/snippet}
</PageStatus>

<style>
	.review {
		margin-bottom: 0.8rem;
	}
	.review h2 {
		margin-top: 0;
	}
</style>
