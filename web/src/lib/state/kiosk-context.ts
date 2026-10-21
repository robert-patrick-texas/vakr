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

/**
 * Attributes that put a table's scroll area in the tab order on a kiosk display (WCAG 2.1.1).
 *
 * Elsewhere a row's links are focusable, and a keyboard can scroll a wide
 * table by tabbing through them. A kiosk renders those links as text, so a
 * table wider than the screen would be a scroll area no keyboard can reach
 * (axe `scrollable-region-focusable`); the wrapper becomes a named,
 * focusable region instead.
 *
 * @param kiosk - The component's {@link inKiosk} answer, taken while it initialised, or
 *   true for a table that holds no link on any page.
 * @param label - The table's name, usually its caption.
 * @returns Attributes to spread on the wrapper; none outside a kiosk.
 */
export function scrollRegion(kiosk: boolean, label: string): Record<string, string | number> {
	return kiosk ? { tabindex: 0, role: 'region', 'aria-label': label } : {};
}
