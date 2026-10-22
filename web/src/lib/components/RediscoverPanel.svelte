<!--
@component
"Rediscover now" on the device page (DISC-003, DISC-010; admins only, E.31, E.32).

The button asks vakr-api for a manual rediscovery and gets the operation
back at once; the page then follows it every 2 s while it is queued or
running and shows how it ended: the interfaces found and the identity
changes, or why it failed. When it ends, the device's data is refreshed.
The newest operation of the device is shown even when someone else
started it, and the button stays disabled while one is active, so a
second click never queues a second walk. The request carries the CSRF
token and is never retried; the API checks the role again. The parent
renders this only for admins.

Example: `<RediscoverPanel deviceId={id} />`
-->
<script lang="ts">
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap } from '#lib/api/client.ts';
	import { ApiFailure } from '#lib/api/errors.ts';
	import type { OperationOut } from '#lib/api/types.ts';
	import { formatInstant } from '#lib/format/time.ts';
	import { isActive, outcomeText } from '#lib/operations/words.ts';
	import { operationQuery, operationsQuery } from '#lib/query/reads-operations.ts';

	interface Props {
		/** The device's UUID. */
		deviceId: string;
	}

	const { deviceId }: Props = $props();
	const client = useQueryClient();
	let followId = $state<string | null>(null);
	let refusal = $state<string | null>(null);
	let seenActive = $state(false);
	const latest = createQuery(() => operationsQuery(deviceId, 1));
	const followed = createQuery(() => ({
		...operationQuery(followId ?? ''),
		enabled: followId !== null
	}));
	const op = $derived<OperationOut | null>(followed.data ?? latest.data?.rows[0] ?? null);

	const ask = createMutation(() => ({
		mutationFn: () =>
			unwrap(
				api.POST('/api/v1/devices/{device_uuid}/rediscovery', {
					params: { path: { device_uuid: deviceId } }
				})
			),
		onSuccess: (answer: OperationOut) => {
			refusal = null;
			followId = answer.id;
			client.setQueryData(['operation', answer.id], answer);
			announce('Rediscovery requested');
		},
		onError: (error: unknown) => {
			refusal = explain(error);
			announce(refusal);
		}
	}));

	$effect(() => {
		if (op === null) return;
		if (isActive(op)) {
			seenActive = true;
		} else if (seenActive) {
			seenActive = false;
			announce(outcomeText(op));
			void client.invalidateQueries({ queryKey: ['device', deviceId] });
			void client.invalidateQueries({ queryKey: ['device-interfaces', deviceId] });
			void client.invalidateQueries({ queryKey: ['operations'] });
		}
	});

	/**
	 * Say why the API refused the request.
	 *
	 * @param error - What the request threw.
	 * @returns One sentence.
	 */
	function explain(error: unknown): string {
		if (!(error instanceof ApiFailure)) return 'The request failed.';
		if (error.kind === 'forbidden') return 'Permission denied: only an admin may rediscover.';
		if (error.kind === 'conflict') return `Not possible: ${error.message}.`;
		if (error.kind === 'unavailable')
			return `vakr-api is unavailable: ${error.message}. Nothing was queued.`;
		return `Refused: ${error.message}.`;
	}
</script>

<section class="card" aria-labelledby="rediscover-h" data-testid="rediscovery">
	<h2 id="rediscover-h">Rediscovery</h2>
	<p class="muted">
		Walk the device's interface tables now instead of at its next scheduled rediscovery. A collector
		picks the request up within seconds; new, renumbered, and missing interfaces are reconciled as
		in any rediscovery.
	</p>
	{#if op}
		<p role="status" data-testid="rediscovery-status" data-state={op.state}>
			{outcomeText(op)}.
			<span class="muted">
				Requested by {op.requested_by} at {formatInstant(op.requested_at, 'UTC')} UTC{#if op.finished_at};
					ended {formatInstant(op.finished_at, 'UTC')} UTC{/if}.
			</span>
		</p>
	{/if}
	<button
		type="button"
		disabled={ask.isPending || (op !== null && isActive(op))}
		onclick={() => ask.mutate()}>Rediscover now</button
	>
	{#if refusal}<p class="notice notice-amber" role="alert">{refusal}</p>{/if}
</section>
