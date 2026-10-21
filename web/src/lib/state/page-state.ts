/**
 * The state of a page's data, as UI-014 names it: loading, empty, stale, partial,
 * permission denied, or backend unavailable.
 *
 * `pageState` reads one TanStack query (pending, data, error) plus what the
 * answer itself says, and returns exactly one state for the page to show.
 * Data on screen is never discarded because a refresh failed: the page
 * stays `ready` and a stale note says when the shown answer was fetched and
 * why the refresh failed (UI-015: never imply the data is current). Storage
 * or collector trouble arrives as HTTP 200 with `available: false` or an
 * unhealthy component; `healthNotes` turns it into partial notes that say
 * device states are unknown, never down (invariant 2).
 *
 * @example
 * ```ts
 * const state = pageState(query, { isEmpty: (d) => d.rows.length === 0 });
 * if (state.kind === 'ready' && state.stale) showBanner(state.stale);
 * ```
 *
 * @packageDocumentation
 */
import { ApiFailure, describeFailure } from '#lib/api/errors.ts';
import type { HealthView } from '#lib/api/types.ts';

export type Blocking = 'loading' | 'signed-out' | 'denied' | 'not-found' | 'unavailable' | 'failed';

export type PageState<T> =
	| { kind: Blocking; title: string; detail: string }
	| { kind: 'ready'; data: T; empty: boolean; stale: string | null; partial: string[] };

/** The parts of a TanStack query result `pageState` reads. */
export interface QueryLike<T> {
	data: T | undefined;
	error: unknown;
	isPending: boolean;
	dataUpdatedAt: number;
}

/** What the page knows about its own answer. */
export interface StateRules<T> {
	/** True when the answer holds nothing to show. */
	isEmpty?: (data: T) => boolean;
	/** Notes on what is missing from the answer (truncated, telemetry unavailable). */
	partial?: (data: T) => string[];
	/** A note when the evidence in the answer is old, even though the request succeeded. */
	stale?: (data: T) => string | null;
}

const BLOCKING: Record<string, Blocking> = {
	unauthenticated: 'signed-out',
	forbidden: 'denied',
	not_found: 'not-found',
	unavailable: 'unavailable'
};

/**
 * Format a fetch time for a stale note, in the browser's zone (the note is about the browser).
 *
 * @param epochMs - When the shown answer arrived.
 * @returns Such as `14:05:09`.
 */
export function fetchedAt(epochMs: number): string {
	return new Date(epochMs).toLocaleTimeString(undefined, { hour12: false });
}

/**
 * Decide what the page shows.
 *
 * @param query - The query result.
 * @param rules - The page's own empty, partial, and stale rules.
 * @returns One state.
 */
export function pageState<T>(query: QueryLike<T>, rules: StateRules<T> = {}): PageState<T> {
	const data = query.data;
	if (data === undefined) {
		if (query.error === null || query.error === undefined) {
			return { kind: 'loading', title: 'Loading', detail: 'Asking vakr-api…' };
		}
		const { title, detail } = describeFailure(query.error);
		const kind = query.error instanceof ApiFailure ? BLOCKING[query.error.kind] : undefined;
		return { kind: kind ?? 'failed', title, detail };
	}
	const notes: string[] = [];
	if (query.error !== null && query.error !== undefined) {
		const { detail } = describeFailure(query.error);
		notes.push(
			`The last refresh failed (${detail}); showing the answer from ${fetchedAt(query.dataUpdatedAt)}.`
		);
	}
	const evidence = rules.stale?.(data) ?? null;
	if (evidence) notes.push(evidence);
	return {
		kind: 'ready',
		data,
		empty: rules.isEmpty?.(data) ?? false,
		stale: notes.length > 0 ? notes.join(' ') : null,
		partial: rules.partial?.(data) ?? []
	};
}

/**
 * Turn the API's health of its stores and collectors into partial notes (UI-014, UI-015).
 *
 * @param health - `health` of a list or detail answer.
 * @returns One sentence per unhealthy part; empty when everything is healthy.
 */
export function healthNotes(health: HealthView | null | undefined): string[] {
	if (!health) return [];
	const notes: string[] = [];
	if (health.metadata.value !== 'ok') {
		notes.push(`Inventory database: ${health.metadata.text}.`);
	}
	if (health.telemetry.value !== 'ok') {
		notes.push(
			`Telemetry unavailable (${health.telemetry.reason ?? health.telemetry.text}): ` +
				'devices without fresh evidence show Unknown, not Down.'
		);
	}
	for (const c of health.collectors) {
		if (c.state.value === 'healthy') continue;
		const why = c.state.value === null ? (c.state.reason ?? 'no health report') : c.state.text;
		notes.push(`Collector ${c.position}: ${why}; its devices' states are less certain.`);
	}
	return notes;
}

/** The completeness fields that list, graph, and ranking answers share. */
export interface Completeness {
	truncated?: boolean;
	available?: boolean;
	reason?: string | null;
}

/**
 * Notes for an answer that the API cut short or could not compute.
 *
 * @param answer - Any answer with `truncated` and, for telemetry views, `available`/`reason`.
 * @param what - What was cut short, such as `devices`.
 * @returns Notes; empty when the answer is complete.
 */
export function completenessNotes(answer: Completeness, what: string): string[] {
	const notes: string[] = [];
	if (answer.available === false) {
		notes.push(`Not available: ${answer.reason ?? 'telemetry cannot be read'}.`);
	}
	if (answer.truncated)
		notes.push(`Only part of the ${what} is shown: the answer reached its bound.`);
	return notes;
}
