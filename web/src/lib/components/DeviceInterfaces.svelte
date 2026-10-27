<!--
@component
A device's interfaces: primary interfaces in full, other interfaces on demand, or graph cards
(UI-006, UI-016, UI-017; 12.4; WEB-T03, WEB-T11).

- Table view: the primary-interest interfaces (META-011) are listed in
  full; the other interfaces load only when asked for, in pages of 25,
  searchable, sticky and WAN circuits first, optionally only those not
  yet classified (META-013).
- Graph view: one compact traffic card per interface, in priority order
  (sticky, WAN, high priority, then the rest), `CARDS_PER_PAGE` cards per
  page, each loading its graph only when it scrolls into view.

Example: `<DeviceInterfaces deviceId={id} total={detail.interfaces_total} view="table" />`
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { deviceInterfacesQuery } from '#lib/query/reads.ts';
	import type { GraphParams } from '#lib/query/reads-history.ts';
	import { CursorTrail } from '#lib/state/cursors.svelte.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import InterfaceGraphCard from './InterfaceGraphCard.svelte';
	import InterfaceTable from './InterfaceTable.svelte';
	import PageStatus from './PageStatus.svelte';
	import Pager from './Pager.svelte';

	/** Graph cards per page (UI-016, Appendix E.31). */
	const CARDS_PER_PAGE = 12;
	const OTHER_PAGE = 25;

	interface Props {
		deviceId: string;
		view: 'table' | 'graphs';
		range: GraphParams;
	}

	const { deviceId, view, range }: Props = $props();
	let showOther = $state(false);
	let search = $state('');
	let applied = $state('');
	let unclassified = $state(false);
	const other = new CursorTrail();
	const cards = new CursorTrail();

	const primary = createQuery(() =>
		deviceInterfacesQuery(deviceId, { interest: 'primary', limit: 500 })
	);
	const rest = createQuery(() => ({
		...deviceInterfacesQuery(deviceId, {
			interest: 'secondary',
			search: applied,
			unclassified: unclassified || undefined,
			limit: OTHER_PAGE,
			cursor: other.cursor
		}),
		enabled: view === 'table' && showOther
	}));
	const cardPage = createQuery(() => ({
		...deviceInterfacesQuery(deviceId, { limit: CARDS_PER_PAGE, cursor: cards.cursor }),
		enabled: view === 'graphs'
	}));
	const isEmpty = (d: { rows: unknown[] }) => d.rows.length === 0;
</script>

{#if view === 'table'}
	<h3>Primary interfaces</h3>
	<PageStatus
		state={pageState(primary, { isEmpty })}
		label="Primary interfaces"
		emptyText="This device has no primary-interest interfaces."
	>
		{#snippet children(d)}
			<InterfaceTable caption="Primary interfaces" hideCaption rows={d.rows} />
		{/snippet}
	</PageStatus>

	<h3>Other interfaces</h3>
	{#if !showOther}
		<button type="button" onclick={() => (showOther = true)}>Show other interfaces</button>
	{:else}
		<form
			class="row"
			role="search"
			onsubmit={(e) => {
				e.preventDefault();
				applied = search.trim();
				other.reset();
			}}
		>
			<label>Find an interface <input type="search" maxlength="256" bind:value={search} /></label>
			<button type="submit">Search</button>
			<label
				><input
					type="checkbox"
					checked={unclassified}
					onchange={(e) => {
						unclassified = e.currentTarget.checked;
						other.reset();
					}}
				/> Only not yet classified</label
			>
		</form>
		<PageStatus
			state={pageState(rest, { isEmpty })}
			label="Other interfaces"
			emptyText="No other interface matches."
		>
			{#snippet children(d)}
				<InterfaceTable caption="Other interfaces" hideCaption rows={d.rows} />
				<Pager
					page={d.page}
					shown={d.rows.length}
					pageNumber={other.number}
					noun="interfaces"
					onNext={other.next}
					onPrevious={other.previous}
					onFirst={other.reset}
				/>
			{/snippet}
		</PageStatus>
	{/if}
{:else}
	<PageStatus state={pageState(cardPage, { isEmpty })} label="Interface graphs">
		{#snippet children(d)}
			<div class="cards">
				{#each d.rows as row (row.ref.interface_id)}
					<InterfaceGraphCard {row} {range} />
				{/each}
			</div>
			<Pager
				page={d.page}
				shown={d.rows.length}
				pageNumber={cards.number}
				noun="interface graphs"
				onNext={cards.next}
				onPrevious={cards.previous}
				onFirst={cards.reset}
			/>
		{/snippet}
	</PageStatus>
{/if}

<style>
	.cards {
		display: grid;
		gap: var(--gap);
		grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr));
	}
</style>
