/**
 * The catalog choices that list filters offer: sites, tags, groups, collectors (12.3, UI-009).
 *
 * Call `filterOptions()` while a component initializes; it starts the four
 * cached catalog queries and exposes their names and UUIDs. Until an
 * answer arrives, a list is empty and the filter offers only "Any".
 *
 * @example
 * ```ts
 * const options = filterOptions();
 * options.value.sites; // [{ id, name }, ...]
 * ```
 *
 * @packageDocumentation
 */
import { createQuery } from '@tanstack/svelte-query';
import type { FilterOptions } from '#lib/state/filters.ts';
import { collectorsQuery, labelsQuery, sitesQuery } from './reads-catalog.ts';

/**
 * Start the catalog queries and expose the filter choices.
 *
 * @returns An object whose `value` is the current choices.
 */
export function filterOptions(): { readonly value: FilterOptions } {
	const sites = createQuery(() => sitesQuery());
	const tags = createQuery(() => labelsQuery('tags'));
	const groups = createQuery(() => labelsQuery('groups'));
	const collectors = createQuery(() => ({ ...collectorsQuery(), refetchInterval: false as const }));
	const value = $derived<FilterOptions>({
		sites: sites.data?.items.map((s) => ({ id: s.id, name: s.name })) ?? [],
		tags: tags.data?.items.map((t) => ({ id: t.id, name: t.name })) ?? [],
		groups: groups.data?.items.map((g) => ({ id: g.id, name: g.name })) ?? [],
		collectors: collectors.data?.items.map((c) => ({ id: c.id, name: c.position })) ?? []
	});
	return {
		get value() {
			return value;
		}
	};
}
