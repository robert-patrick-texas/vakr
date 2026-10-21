<!--
@component
Kiosk tokens for wall displays: list, create, and revoke (UI-013, E.12; admins only).

A new token is shown once, in a box with a copy button and the setup
steps, and is gone from the page when the admin closes the box; the list
never holds tokens. Revoking asks for confirmation first and signs every
display that holds the token out at its next request. Every change goes
with the CSRF token and is never retried; the API checks the role again.

Example: `<KioskTokens />`
-->
<script lang="ts">
	import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { api, unwrap, unwrapEmpty } from '#lib/api/client.ts';
	import { ApiFailure } from '#lib/api/errors.ts';
	import type { KioskIssued, KioskTokenOut } from '#lib/api/types.ts';
	import { formatDate } from '#lib/kiosk/expiry.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from './PageStatus.svelte';

	const LIFETIMES = [
		['7d', '7 days'],
		['30d', '30 days'],
		['90d', '90 days'],
		['365d', '1 year']
	] as const;
	const KEY = ['kiosk-tokens'] as const;
	const client = useQueryClient();
	const tokens = createQuery(() => ({
		queryKey: KEY,
		queryFn: () => unwrap(api.GET('/api/v1/kiosk-tokens', { params: { query: { limit: 100 } } }))
	}));
	const shown = $derived(pageState(tokens, { isEmpty: (d) => d.rows.length === 0 }));
	let label = $state('');
	let lifetime = $state('30d');
	let issued = $state<KioskIssued | null>(null);
	let confirming = $state<KioskTokenOut | null>(null);
	let outcome = $state<string | null>(null);

	/**
	 * Say why the API refused a change.
	 *
	 * @param error - What the request threw.
	 * @returns One sentence.
	 */
	function explain(error: unknown): string {
		if (!(error instanceof ApiFailure)) return 'The change failed.';
		if (error.kind === 'forbidden') return 'Permission denied: only an admin manages kiosk tokens.';
		if (error.kind === 'conflict')
			return 'A kiosk token with this label exists; choose another label.';
		return `Refused: ${error.message}.`;
	}

	/**
	 * Report an outcome on the page and to screen readers.
	 *
	 * @param text - One sentence.
	 */
	function tell(text: string): void {
		outcome = text;
		announce(text);
	}

	const create = createMutation(() => ({
		mutationFn: () =>
			unwrap(api.POST('/api/v1/kiosk-tokens', { body: { label: label.trim(), lifetime } })),
		onSuccess: (answer: KioskIssued) => {
			issued = answer;
			label = '';
			tell(`Created kiosk token ${answer.kiosk.label}. Copy it now: it is shown only once.`);
			void client.invalidateQueries({ queryKey: KEY });
		},
		onError: (error: unknown) => tell(explain(error))
	}));

	const revoke = createMutation(() => ({
		mutationFn: (t: KioskTokenOut) =>
			unwrapEmpty(
				api.DELETE('/api/v1/kiosk-tokens/{kiosk_uuid}', { params: { path: { kiosk_uuid: t.id } } })
			),
		onSuccess: (_answer: unknown, t: KioskTokenOut) => {
			confirming = null;
			tell(`Revoked kiosk token ${t.label}; displays holding it are signed out.`);
			void client.invalidateQueries({ queryKey: KEY });
		},
		onError: (error: unknown) => tell(explain(error))
	}));

	/**
	 * Copy the new token to the clipboard.
	 */
	async function copy(): Promise<void> {
		if (issued === null) return;
		try {
			await navigator.clipboard.writeText(issued.token);
			tell('Copied the token.');
		} catch {
			tell('The browser did not allow copying; select the token and copy it by hand.');
		}
	}
</script>

<section aria-labelledby="kiosk-h" class="card">
	<h2 id="kiosk-h">Kiosk tokens</h2>
	<p>
		A kiosk token signs one wall display in, read-only, without a password. Paste it at
		<code>/kiosk/setup</code> on the display. See the kiosk guide for setting up a display.
	</p>
	<form
		class="row"
		onsubmit={(e) => {
			e.preventDefault();
			create.mutate();
		}}
	>
		<label for="kiosk-label">Display label</label>
		<input id="kiosk-label" maxlength="64" required bind:value={label} placeholder="noc-wall" />
		<label for="kiosk-lifetime">Lifetime</label>
		<select id="kiosk-lifetime" bind:value={lifetime}>
			{#each LIFETIMES as [value, text] (value)}<option {value}>{text}</option>{/each}
		</select>
		<button type="submit" disabled={create.isPending || label.trim() === ''}>Create token</button>
	</form>

	{#if issued}
		<div class="notice notice-amber issued" role="region" aria-label="New kiosk token">
			<p><strong>Kiosk token for {issued.kiosk.label}</strong>, shown only now:</p>
			<p><code class="token" data-testid="kiosk-new-token">{issued.token}</code></p>
			<p>
				<button type="button" onclick={copy}>Copy</button>
				<button type="button" onclick={() => (issued = null)}>Done, hide it</button>
			</p>
			<p>
				On the display, open <code>{issued.setup_path}</code> on this vakr address and paste the
				token. It expires {formatDate(new Date(issued.kiosk.expires_at))}.
			</p>
		</div>
	{/if}
	{#if outcome}<p role="status" data-testid="kiosk-outcome">{outcome}</p>{/if}

	<PageStatus state={shown} label="Kiosk tokens" emptyText="No kiosk tokens yet.">
		{#snippet children(d)}
			<div class="table-wrap">
				<table class="density-compact">
					<caption>Kiosk tokens by label</caption>
					<thead>
						<tr>
							<th scope="col">Label</th>
							<th scope="col">State</th>
							<th scope="col">Expires</th>
							<th scope="col">Last used</th>
							<th scope="col">Created by</th>
							<th scope="col"><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each d.rows as t (t.id)}
							<tr>
								<th scope="row">{t.label}</th>
								<td>{t.live ? 'live' : 'expired'}</td>
								<td>{formatDate(new Date(t.expires_at))}</td>
								<td>{t.last_used_at ? formatDate(new Date(t.last_used_at)) : 'never'}</td>
								<td>{t.created_by}</td>
								<td>
									{#if confirming?.id === t.id}
										<button type="button" onclick={() => revoke.mutate(t)}
											>Yes, revoke {t.label}</button
										>
										<button type="button" onclick={() => (confirming = null)}>Cancel</button>
									{:else}
										<button type="button" onclick={() => (confirming = t)}>Revoke</button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/snippet}
	</PageStatus>
</section>

<style>
	.token {
		font-size: 1.05rem;
		word-break: break-all;
	}
	.issued {
		margin: 0.8rem 0;
	}
</style>
