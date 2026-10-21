/**
 * Whether a component renders inside a kiosk display (UI-013).
 *
 * The kiosk layout marks its subtree; shared components ask before they
 * render a link. On a wall display nobody clicks, and a stray tap must not
 * navigate the display away, so rows and names render as plain text there
 * (`Link.svelte`). Both functions must run while a component initialises,
 * as Svelte contexts do.
 *
 * @example
 * ```ts
 * setKioskMode(); // in the kiosk layout
 * const plain = inKiosk(); // in a shared component
 * ```
 *
 * @packageDocumentation
 */
import { getContext, setContext } from 'svelte';

const KEY = Symbol('vakr.kiosk');

/**
 * Mark the calling component's subtree as a kiosk display.
 */
export function setKioskMode(): void {
	setContext(KEY, true);
}

/**
 * Tell whether the calling component is inside a kiosk display.
 *
 * @returns True under the kiosk layout.
 */
export function inKiosk(): boolean {
	return getContext<boolean | undefined>(KEY) === true;
}
