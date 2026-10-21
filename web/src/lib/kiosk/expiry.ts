/**
 * When a kiosk display should warn that its token ends soon (Appendix E.31).
 *
 * A kiosk token lives 30 days by default and nobody watches the display's
 * sign-in, so for the last 7 days the display says on screen when the
 * token expires, which gives an admin a week to hand over a new one.
 *
 * @example
 * ```ts
 * expiresSoon('2026-10-25T00:00:00Z', Date.parse('2026-10-21T00:00:00Z')); // Date
 * ```
 *
 * @packageDocumentation
 */

/** Warn this long before the token expires. */
export const EXPIRY_WARNING_MS = 7 * 24 * 3600 * 1000;

/**
 * Return the expiry when it is within the warning window.
 *
 * @param expiresAt - The token's expiry (ISO 8601), or null when unknown.
 * @param now - Milliseconds since the epoch.
 * @returns The expiry, or null when it is further away or unknown.
 */
export function expiresSoon(expiresAt: string | null, now: number): Date | null {
	if (expiresAt === null) return null;
	const at = Date.parse(expiresAt);
	return Number.isFinite(at) && at - now <= EXPIRY_WARNING_MS ? new Date(at) : null;
}

/**
 * Format the expiry for the display, in the browser's zone (the display is the reader).
 *
 * @param at - The expiry.
 * @returns Such as `25 Oct 2026, 14:03`.
 */
export function formatDate(at: Date): string {
	return at.toLocaleString(undefined, {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
		hour12: false
	});
}
