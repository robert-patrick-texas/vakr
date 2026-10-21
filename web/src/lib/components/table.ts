/**
 * Column definitions for `DataTable` (12.3, 12.5, 12.8).
 *
 * A column names its header, renders each cell with a Svelte snippet, and,
 * when the API can order by it, names the server's `sort` key and what that
 * order means (`ascending` or `descending`, and words for the announcement).
 *
 * @example
 * ```ts
 * const name: Column<DeviceRow> = { id: 'name', header: 'Name', cell: nameCell, sortKey: 'name',
 *   sortDirection: 'ascending', sortWords: 'A to Z' };
 * ```
 *
 * @packageDocumentation
 */
import type { Snippet } from 'svelte';

/** One column of a `DataTable`. */
export interface Column<R> {
	id: string;
	header: string;
	cell: Snippet<[R]>;
	sortKey?: string;
	sortDirection?: 'ascending' | 'descending';
	sortWords?: string;
	numeric?: boolean;
}

/**
 * The `aria-sort` value of a column's header.
 *
 * @param column - The column.
 * @param sort - The active server sort key.
 * @returns `ascending`, `descending`, or `none` for a sortable column; `undefined` otherwise.
 */
export function ariaSort<R>(
	column: Column<R> | undefined,
	sort: string | null
): 'ascending' | 'descending' | 'none' | undefined {
	if (column?.sortKey === undefined) return undefined;
	return column.sortKey === sort ? (column.sortDirection ?? 'ascending') : 'none';
}
