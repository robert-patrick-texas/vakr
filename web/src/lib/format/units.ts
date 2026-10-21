/**
 * Network units with SI prefixes, as 12.6 asks: `kbit/s`, `Mbit/s`, `Gbit/s` (decimal, 1000).
 *
 * Rates in API answers are bits per second (rankings) or arrive with the
 * API's own display text (`Reading.text`). Where the page formats a number
 * itself, these functions do it, and a missing value stays `—`, never 0
 * (invariant 1).
 *
 * @example
 * ```ts
 * bitRate(1_250_000); // '1.25 Mbit/s'
 * ```
 *
 * @packageDocumentation
 */

const DASH = '—';
const PREFIXES = [
	[1e12, 'Tbit/s'],
	[1e9, 'Gbit/s'],
	[1e6, 'Mbit/s'],
	[1e3, 'kbit/s']
] as const;

/**
 * Format a number with three significant digits.
 *
 * @param value - The number.
 * @returns Such as `1.25`, `12.5`, `125`.
 */
export function threeDigits(value: number): string {
	const abs = Math.abs(value);
	if (abs >= 100) return value.toFixed(0);
	if (abs >= 10) return value.toFixed(1);
	return value.toFixed(2);
}

/**
 * Format bits per second with an SI prefix.
 *
 * @param bps - Bits per second, or `null` when unknown.
 * @returns Such as `950 bit/s`, `1.25 Mbit/s`, or `—`.
 */
export function bitRate(bps: number | null | undefined): string {
	if (bps === null || bps === undefined || !Number.isFinite(bps)) return DASH;
	for (const [scale, unit] of PREFIXES) {
		if (Math.abs(bps) >= scale) return `${threeDigits(bps / scale)} ${unit}`;
	}
	return `${threeDigits(bps)} bit/s`;
}

/**
 * Format a percentage.
 *
 * @param pct - Percent (0–100, may exceed 100), or `null`.
 * @returns Such as `42.1%`, or `—`.
 */
export function percent(pct: number | null | undefined): string {
	if (pct === null || pct === undefined || !Number.isFinite(pct)) return DASH;
	return `${threeDigits(pct)}%`;
}

/**
 * Format an integer count with thousands separators.
 *
 * @param n - The count, or `null`.
 * @returns Such as `12,345`, or `—`.
 */
export function count(n: number | null | undefined): string {
	if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
	return Math.round(n).toLocaleString('en-US');
}
