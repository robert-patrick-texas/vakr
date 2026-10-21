<!--
@component
Graph range presets and a bounded custom range (12.6, API-006, E.28).

The presets are buttons in a labelled group, the chosen one marked with
`aria-pressed`. The custom range takes wall-clock times in the display
zone (named beside the inputs), checks the E.28 bounds before asking the
API, and says why a range is refused.

Example: `<RangePicker range={range} zone="America/Chicago" onChange={(r) => setRange(r)} />`
-->
<script lang="ts">
	import {
		PRESETS,
		checkCustom,
		epochToWall,
		wallToEpoch,
		type GraphRange
	} from '#lib/state/range.ts';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { knownZone } from '#lib/format/time.ts';

	interface Props {
		range: GraphRange;
		zone: string;
		onChange: (range: GraphRange) => void;
	}

	const { range, zone: named, onChange }: Props = $props();
	// A zone the browser does not know cannot convert wall time; UTC is named instead.
	const zone = $derived(knownZone(named) ? named : 'UTC');
	let open = $state(false);
	let from = $state('');
	let to = $state('');
	let problem = $state<string | null>(null);

	/**
	 * Open the custom-range form, filled with the last 24 hours.
	 */
	function openCustom(): void {
		const now = Date.now();
		from =
			range.preset === null
				? epochToWall(Date.parse(range.start), zone)
				: epochToWall(now - 86_400_000, zone);
		to = range.preset === null ? epochToWall(Date.parse(range.end), zone) : epochToWall(now, zone);
		problem = null;
		open = true;
	}

	/**
	 * Apply the custom range if it is within bounds.
	 *
	 * @param event - The form submission.
	 */
	function apply(event: SubmitEvent): void {
		event.preventDefault();
		const start = wallToEpoch(from, zone);
		const end = wallToEpoch(to, zone);
		problem = checkCustom(start, end, Date.now());
		if (problem) return;
		onChange({
			preset: null,
			start: new Date(start).toISOString(),
			end: new Date(end).toISOString()
		});
		announce('Custom range applied');
		open = false;
	}
</script>

<div class="range row" role="group" aria-label="Graph range">
	{#each PRESETS as preset (preset)}
		<button
			type="button"
			aria-pressed={range.preset === preset}
			onclick={() => {
				onChange({ preset });
				announce(`Range ${preset}`);
			}}>{preset}</button
		>
	{/each}
	<button
		type="button"
		aria-pressed={range.preset === null}
		aria-expanded={open}
		onclick={openCustom}>Custom…</button
	>
</div>
{#if open}
	<form class="custom row" onsubmit={apply}>
		<label>From <input type="datetime-local" bind:value={from} required /></label>
		<label>To <input type="datetime-local" bind:value={to} required /></label>
		<span class="muted">({zone})</span>
		<button type="submit">Apply</button>
		<button type="button" onclick={() => (open = false)}>Cancel</button>
		{#if problem}<p class="notice notice-red" role="alert">{problem}</p>{/if}
	</form>
{/if}

<style>
	.range button {
		min-width: 3rem;
	}
	.custom {
		margin-top: 0.4rem;
	}
</style>
