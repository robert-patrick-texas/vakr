<!--
@component
The UI-014 states of one block of data: loading, empty, stale, partial, permission denied,
signed out, not found, and backend unavailable.

Give it the result of `pageState(...)`. Blocking states replace the content
with a message; `ready` renders `children` with the data, under a stale
note (the last refresh failed, or the evidence is old) and partial notes
(telemetry unavailable, an unhealthy collector, a truncated answer). The
notes are `role="status"`: a screen reader hears them when they appear or
change, not at every refresh, because unchanged text is not re-rendered
(12.8). Nothing here says a device is down: backend trouble is named as
backend trouble (UI-015).

Example:
```svelte
<PageStatus state={pageState(query)} emptyText="No devices match.">
  {#snippet children(data)}<DeviceTable rows={data.rows} />{/snippet}
</PageStatus>
```
-->
<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';
	import type { PageState } from '#lib/state/page-state.ts';

	interface Props {
		state: PageState<T>;
		children: Snippet<[T]>;
		/** Shown instead of `children` when the answer holds nothing. */
		emptyText?: string;
		/** Heading level text for screen readers, such as `Devices`. */
		label?: string;
	}

	const { state, children, emptyText = 'Nothing to show.', label = '' }: Props = $props();
	const tone: Record<string, string> = {
		loading: 'gray',
		'signed-out': 'gray',
		denied: 'amber',
		'not-found': 'gray',
		unavailable: 'red',
		failed: 'amber'
	};
</script>

{#if state.kind === 'ready'}
	{#if state.stale}
		<p class="notice notice-amber" role="status" data-state="stale">
			<strong>Stale:</strong>
			{state.stale}
		</p>
	{/if}
	{#if state.partial.length > 0}
		<div class="notice notice-amber" role="status" data-state="partial">
			<strong>Partial{label ? ` ${label.toLowerCase()}` : ''}:</strong>
			<ul>
				{#each state.partial as note (note)}<li>{note}</li>{/each}
			</ul>
		</div>
	{/if}
	{#if state.empty}
		<p class="notice notice-gray" data-state="empty">{emptyText}</p>
	{:else}
		{@render children(state.data)}
	{/if}
{:else if state.kind === 'loading'}
	<p class="notice notice-gray muted" aria-busy="true" data-state="loading">
		Loading{label ? ` ${label.toLowerCase()}` : ''}…
	</p>
{:else}
	<div class="notice notice-{tone[state.kind] ?? 'amber'}" role="alert" data-state={state.kind}>
		<strong>{state.title}.</strong>
		{state.detail}
		{#if state.kind === 'signed-out'}<a href="/login">Sign in</a>{/if}
	</div>
{/if}

<style>
	ul {
		margin: 0.25rem 0 0;
		padding-left: 1.2rem;
	}
</style>
