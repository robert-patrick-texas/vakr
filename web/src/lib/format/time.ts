/**
 * Times shown in vakr's configured display zone, never silently in the browser's (TIME-003, 12.6).
 *
 * The API sends every time as UTC ISO 8601 and names the display zone in
 * each graph and ranking answer (`window.timezone`, E.28 #6), with the
 * subject's site zone beside it when the site has one. Pages format times
 * in that zone and say which zone it is. A zone the browser does not know
 * falls back to UTC, and the label says so.
 *
 * @example
 * ```ts
 * formatInstant('2026-10-20T11:20:00Z', 'UTC'); // '2026-10-20 11:20:00'
 * ```
 *
 * @packageDocumentation
 */

const cache = new Map<string, Intl.DateTimeFormat>();

/**
 * Tell whether the browser knows an IANA zone name.
 *
 * @param zone - Such as `America/Chicago`.
 * @returns True when `Intl` accepts it.
 */
export function knownZone(zone: string): boolean {
	try {
		new Intl.DateTimeFormat('en-US', { timeZone: zone });
		return true;
	} catch {
		return false;
	}
}

/**
 * Return a cached formatter for a zone (UTC when the zone is unknown).
 *
 * @param zone - The display zone.
 * @returns A 24-hour, numeric formatter.
 */
function formatter(zone: string): Intl.DateTimeFormat {
	const name = knownZone(zone) ? zone : 'UTC';
	let f = cache.get(name);
	if (f === undefined) {
		f = new Intl.DateTimeFormat('en-CA', {
			timeZone: name,
			year: 'numeric',
			month: '2-digit',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit',
			hourCycle: 'h23'
		});
		cache.set(name, f);
	}
	return f;
}

/**
 * Format an instant as `YYYY-MM-DD HH:MM:SS` in a zone.
 *
 * @param iso - UTC ISO 8601 time, or a `Date`.
 * @param zone - The display zone.
 * @returns The formatted time, or `—` for a missing time.
 */
export function formatInstant(iso: string | Date | null | undefined, zone: string): string {
	if (iso === null || iso === undefined) return '—';
	const date = typeof iso === 'string' ? new Date(iso) : iso;
	if (Number.isNaN(date.getTime())) return '—';
	const parts = Object.fromEntries(
		formatter(zone)
			.formatToParts(date)
			.map((p) => [p.type, p.value])
	);
	return `${parts['year']}-${parts['month']}-${parts['day']} ${parts['hour']}:${parts['minute']}:${parts['second']}`;
}

/**
 * Name the zone for a disclosure line.
 *
 * @param zone - The display zone.
 * @param siteZone - The subject's site zone, if different.
 * @returns Such as `Times in America/Chicago (site: Europe/Berlin)`.
 */
export function zoneLabel(zone: string, siteZone: string | null = null): string {
	const shown = knownZone(zone) ? zone : `UTC (the browser does not know ${zone})`;
	const site = siteZone && siteZone !== zone ? ` (site zone: ${siteZone})` : '';
	return `Times in ${shown}${site}`;
}

/**
 * Express seconds as a short age.
 *
 * @param seconds - Elapsed seconds, or `null`.
 * @returns Such as `45 s`, `12 min`, `3 h`, `2 d`; `—` when unknown.
 */
export function age(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
	if (seconds < 90) return `${Math.round(seconds)} s`;
	if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
	if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
	return `${Math.round(seconds / 86400)} d`;
}
