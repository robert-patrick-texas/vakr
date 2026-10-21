/**
 * One polite live region for the whole app (12.8: sortable-state announcements).
 *
 * The layout renders `announcement.text` in a visually hidden
 * `aria-live="polite"` element. Components call `announce` for changes the
 * user asked for, such as a new sort order or a page of results; refreshes
 * never call it, so unchanged content is not re-announced.
 *
 * @example
 * ```ts
 * announce('Sorted by name, A to Z');
 * ```
 *
 * @packageDocumentation
 */

/** The text of the live region. */
export const announcement = $state({ text: '' });

/**
 * Announce a change to screen-reader users.
 *
 * @param text - One short sentence.
 */
export function announce(text: string): void {
	// Clearing first makes a repeated identical message audible again.
	announcement.text = '';
	queueMicrotask(() => {
		announcement.text = text;
	});
}
