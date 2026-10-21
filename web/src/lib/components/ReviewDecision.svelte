<!--
@component
An admin's answer to one identity review: map the row to an existing interface, or give it a
new identity (IFID-007; the one admin write in API v1).

The controls exist only for admins (the API checks again: an operator who
reaches them anyway gets 403, shown as permission denied). Each decision
asks for confirmation first, is sent once with the CSRF token, and is
never retried. A review someone else already decided answers 409, which
is shown as such.

Example: `<ReviewDecision review={r} />`
-->
<script lang="ts">
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { api, unwrap } from '#lib/api/client.ts';
	import { ApiFailure } from '#lib/api/errors.ts';
	import type { ReviewOut } from '#lib/api/types.ts';
	import { announce } from '#lib/a11y/announce.svelte.ts';

	interface Props {
		review: ReviewOut;
	}

	type Choice = { decision: 'map' | 'new'; interfaceId: string; text: string };

	const { review }: Props = $props();
	const client = useQueryClient();
	let pending = $state<Choice | null>(null);
	let outcome = $state<string | null>(null);

	const decide = createMutation(() => ({
		mutationFn: (choice: Choice) =>
			unwrap(
				api.POST('/api/v1/interfaces/{interface_uuid}/identity-resolution', {
					params: { path: { interface_uuid: choice.interfaceId } },
					body: { review_id: review.id, decision: choice.decision }
				})
			),
		onSuccess: (_answer, choice) => {
			outcome = `Done: ${choice.text}.`;
			announce(outcome);
			void client.invalidateQueries({ queryKey: ['reviews'] });
		},
		onError: (error) => {
			outcome = explain(error);
			announce(outcome);
		}
	}));

	/**
	 * Say why a decision was refused.
	 *
	 * @param error - What the API answered.
	 * @returns One sentence.
	 */
	function explain(error: unknown): string {
		if (!(error instanceof ApiFailure)) return 'The decision failed.';
		if (error.kind === 'forbidden') return 'Permission denied: only an admin may decide reviews.';
		if (error.kind === 'conflict') return `Not possible: ${error.message}. Reload the reviews.`;
		if (error.kind === 'unavailable')
			return `vakr-api is unavailable: ${error.message}. Nothing changed.`;
		return `Refused: ${error.message}.`;
	}
</script>

<div class="decision" role="group" aria-label="Decision for ifIndex {review.if_index}">
	{#if pending === null}
		{#each review.candidates as c (c.interface_id)}
			<button
				type="button"
				onclick={() =>
					(pending = {
						decision: 'map',
						interfaceId: c.interface_id,
						text: `ifIndex ${review.if_index} is ${c.name}`
					})}>It is {c.name}</button
			>
		{/each}
		{#if review.candidates.length > 0}
			<button
				type="button"
				onclick={() =>
					(pending = {
						decision: 'new',
						interfaceId: review.candidates[0]?.interface_id ?? '',
						text: `ifIndex ${review.if_index} is a new interface`
					})}>It is a new interface</button
			>
		{:else}
			<p class="muted">No candidate: decide with <code>vakr interface review</code> on the node.</p>
		{/if}
	{:else}
		<p role="alert" class="notice notice-amber">
			Confirm: {pending.text}? This changes the interface's identity history.
		</p>
		<button
			type="button"
			disabled={decide.isPending}
			onclick={() => pending && decide.mutate(pending)}>Confirm</button
		>
		<button type="button" disabled={decide.isPending} onclick={() => (pending = null)}
			>Cancel</button
		>
	{/if}
	{#if outcome}<p role="status" data-testid="decision-outcome">{outcome}</p>{/if}
</div>

<style>
	.decision {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		align-items: center;
	}
</style>
