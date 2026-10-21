<!--
@component
A table of server-ordered rows on TanStack Table, with sortable headers that say their state
(12.3, 12.8, 12.9; ADR-035 decision 7).

The API orders and pages every list (API-005), so sorting is manual: a
sortable header is a button that asks the page to change the server's
`sort` parameter, and the header carries `aria-sort`. The change is
announced in words (for example "Sorted by Name, A to Z"). Rows are keyed
by UUID, so a refresh updates cells in place and keeps keyboard focus.

Example:
```svelte
<DataTable caption="Devices" rows={page.rows} {columns} rowKey={(r) => r.device_id}
  sort="status" onSort={(key) => setSort(key)} />
```
-->
<script lang="ts" generics="T extends RowData">
	import {
		createTable,
		FlexRender,
		renderSnippet,
		rowSortingFeature,
		tableFeatures,
		type ColumnDef,
		type RowData
	} from '@tanstack/svelte-table';
	import { announce } from '#lib/a11y/announce.svelte.ts';
	import { ariaSort, type Column } from './table.ts';

	interface Props {
		caption: string;
		rows: T[];
		columns: Column<T>[];
		rowKey: (row: T) => string;
		sort?: string | null;
		onSort?: (key: string) => void;
		compact?: boolean;
		hideCaption?: boolean;
	}

	const {
		caption,
		rows,
		columns,
		rowKey,
		sort = null,
		onSort,
		compact = false,
		hideCaption = false
	}: Props = $props();

	const features = tableFeatures({ rowSortingFeature });
	// The page passes a constant column list, so the definitions are built once.
	// svelte-ignore state_referenced_locally
	const fixed = columns;
	const byId = new Map(fixed.map((c) => [c.id, c]));
	const defs: ColumnDef<typeof features, T>[] = fixed.map((c) => ({
		id: c.id,
		header: c.header,
		enableSorting: c.sortKey !== undefined,
		accessorFn: (row: T) => rowKey(row),
		cell: (ctx) => renderSnippet(c.cell, ctx.row.original)
	}));
	const table = createTable({
		features,
		columns: defs,
		get data() {
			return rows;
		},
		getRowId: (row: T) => rowKey(row),
		manualSorting: true,
		state: {
			get sorting() {
				const active = fixed.find((c) => c.sortKey !== undefined && c.sortKey === sort);
				return active ? [{ id: active.id, desc: active.sortDirection === 'descending' }] : [];
			}
		}
	});

	/**
	 * Ask the page to sort by a column, and say so.
	 *
	 * @param column - The column.
	 */
	function choose(column: Column<T>): void {
		if (column.sortKey === undefined || onSort === undefined) return;
		onSort(column.sortKey);
		announce(`Sorted by ${column.header}${column.sortWords ? `, ${column.sortWords}` : ''}`);
	}
</script>

<div class="table-wrap">
	<table class:density-compact={compact}>
		<caption class:sr-only={hideCaption}>{caption}</caption>
		<thead>
			{#each table.getHeaderGroups() as group (group.id)}
				<tr>
					{#each group.headers as header (header.id)}
						{@const column = byId.get(header.column.id)}
						<th scope="col" aria-sort={ariaSort(column, sort)} class:num={column?.numeric}>
							{#if column?.sortKey !== undefined && onSort}
								<button type="button" class="sort" onclick={() => choose(column)}>
									{column.header}<span class="arrow" aria-hidden="true"
										>{column.sortKey === sort ? '▾' : '↕'}</span
									>
								</button>
							{:else}
								<FlexRender {header} />
							{/if}
						</th>
					{/each}
				</tr>
			{/each}
		</thead>
		<tbody>
			{#each table.getRowModel().rows as row (row.id)}
				<tr>
					{#each row.getAllCells() as cell (cell.id)}
						<td class:num={byId.get(cell.column.id)?.numeric}><FlexRender {cell} /></td>
					{/each}
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.sort {
		background: none;
		border: none;
		padding: 0;
		font-weight: 600;
		color: inherit;
	}
	.arrow {
		margin-left: 0.3em;
	}
</style>
