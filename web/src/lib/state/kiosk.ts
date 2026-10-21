/**
 * Kiosk display options and the rotation schedule (UI-013, DASH-T06; Appendix E.31).
 *
 * A kiosk URL names its dashboard and, optionally, how often it refreshes:
 * `/kiosk/wan?refresh=60`. A rotation names several dashboards and how long
 * each is shown: `/kiosk/rotate?pages=overview,wan,rankings&every=60`. Every
 * value has a bound (E.31); a value outside it, or an unknown dashboard
 * name, is an error the display shows in full, never a silent fallback,
 * because a wall display is configured once and nobody watches it closely.
 *
 * `RotationSchedule` is the rotation's clock. It counts only time the page
 * was visible (rotation pauses while the tab is hidden), wraps around, and
 * says when the next dashboard's data may be fetched ahead of the switch.
 *
 * @example
 * ```ts
 * const r = rotationOptions(new URLSearchParams('pages=overview,wan&every=30'));
 * if ('pages' in r) new RotationSchedule(r.pages, r.dwellMs);
 * ```
 *
 * @packageDocumentation
 */

const SECOND = 1000;

/** The built-in dashboards a display can show, in the chooser's order. */
export const KIOSK_BOARDS = ['overview', 'high-priority', 'wan', 'rankings'] as const;
export type KioskBoard = (typeof KIOSK_BOARDS)[number];

/** Each dashboard's heading. */
export const BOARD_TITLES: Record<KioskBoard, string> = {
	overview: 'Overview',
	'high-priority': 'High-priority interfaces',
	wan: 'WAN circuits',
	rankings: 'Top utilization and errors'
};

/** `refresh` bounds, seconds (E.31). */
export const REFRESH_MIN_S = 15;
export const REFRESH_MAX_S = 3600;
/** `every` (time each rotated dashboard is shown) bounds and default, seconds (E.31). */
export const DWELL_MIN_S = 15;
export const DWELL_MAX_S = 3600;
export const DWELL_DEFAULT_S = 60;
/** Entries in `pages`: at least two, at most eight; a dashboard may appear more than once. */
export const ROTATE_MIN_PAGES = 2;
export const ROTATE_MAX_PAGES = 8;
/** The next dashboard's data is fetched this long before the switch (no extra requests). */
export const PREFETCH_MS = 5 * SECOND;
/** Ranking windows a kiosk overview or ranking may show. */
export const KIOSK_WINDOWS = ['15m', '1h', '6h', '24h'] as const;
export type KioskWindow = (typeof KIOSK_WINDOWS)[number];

/** The query parameters a kiosk page reads (the page's read-only `URLSearchParams`). */
export interface Params {
	get(name: string): string | null;
}

/** What a kiosk page shows, or why it cannot. */
export type KioskOptions = { refreshMs: number | null; window: KioskWindow } | { error: string };

/**
 * Tell whether a name is a built-in dashboard.
 *
 * @param name - From the URL.
 * @returns True for one of `KIOSK_BOARDS`.
 */
export function isBoard(name: string): name is KioskBoard {
	return (KIOSK_BOARDS as readonly string[]).includes(name);
}

/**
 * Read a whole number of seconds within bounds.
 *
 * @param text - The parameter, or null when absent.
 * @param min - Smallest allowed.
 * @param max - Largest allowed.
 * @returns The seconds, null when absent, or NaN when malformed or out of bounds.
 */
function seconds(text: string | null, min: number, max: number): number | null {
	if (text === null) return null;
	if (!/^\d{1,6}$/.test(text)) return Number.NaN;
	const value = Number(text);
	return value >= min && value <= max ? value : Number.NaN;
}

/**
 * Read `refresh` and `window`, shared by every kiosk page.
 *
 * @param params - The page's query parameters.
 * @returns The options; `refreshMs` null means each dashboard's own interval.
 */
export function kioskOptions(params: Params): KioskOptions {
	const refresh = seconds(params.get('refresh'), REFRESH_MIN_S, REFRESH_MAX_S);
	if (Number.isNaN(refresh)) {
		return {
			error: `refresh must be a whole number of seconds from ${REFRESH_MIN_S} to ${REFRESH_MAX_S}.`
		};
	}
	const asked = params.get('window') ?? '15m';
	const window = KIOSK_WINDOWS.find((w) => w === asked);
	if (window === undefined) return { error: `window must be one of ${KIOSK_WINDOWS.join(', ')}.` };
	return { refreshMs: refresh === null ? null : refresh * SECOND, window };
}

/** A rotation, or why the URL does not describe one. */
export type RotationOptions = { pages: KioskBoard[]; dwellMs: number } | { error: string };

/**
 * Read `pages` and `every` of a rotation URL.
 *
 * @param params - The page's query parameters.
 * @returns The dashboards in order and the time each is shown, or the error to display.
 */
export function rotationOptions(params: Params): RotationOptions {
	const names = (params.get('pages') ?? '').split(',').map((n) => n.trim());
	const known = KIOSK_BOARDS.join(', ');
	if (names.length < ROTATE_MIN_PAGES || names.length > ROTATE_MAX_PAGES || names.includes('')) {
		return {
			error: `pages lists ${ROTATE_MIN_PAGES} to ${ROTATE_MAX_PAGES} dashboards, separated by commas, from: ${known}.`
		};
	}
	const unknown = names.find((n) => !isBoard(n));
	if (unknown !== undefined) {
		return { error: `There is no kiosk dashboard named "${unknown.slice(0, 40)}"; use: ${known}.` };
	}
	const every = seconds(params.get('every'), DWELL_MIN_S, DWELL_MAX_S);
	if (Number.isNaN(every)) {
		return {
			error: `every must be a whole number of seconds from ${DWELL_MIN_S} to ${DWELL_MAX_S}.`
		};
	}
	return { pages: names as KioskBoard[], dwellMs: (every ?? DWELL_DEFAULT_S) * SECOND };
}

/** Where a rotation is, for the display's position line. */
export interface RotationPosition {
	/** One-based position of the dashboard shown. */
	current: number;
	total: number;
	next: KioskBoard;
	/** Whole seconds until the switch, rounded up. */
	nextInS: number;
}

/** The rotation's clock: visible time only, wrapping around. */
export class RotationSchedule {
	readonly pages: KioskBoard[];
	readonly dwellMs: number;
	#index = 0;
	#shownMs = 0;
	#last: number | null = null;

	/**
	 * Start at the first dashboard.
	 *
	 * @param pages - The dashboards in order (at least two).
	 * @param dwellMs - How long each is shown.
	 */
	constructor(pages: KioskBoard[], dwellMs: number) {
		this.pages = pages;
		this.dwellMs = dwellMs;
	}

	/**
	 * The dashboard shown now.
	 *
	 * @returns Its name.
	 */
	get board(): KioskBoard {
		return this.pages[this.#index] as KioskBoard;
	}

	/**
	 * The dashboard that comes next.
	 *
	 * @returns Its name.
	 */
	get next(): KioskBoard {
		return this.pages[(this.#index + 1) % this.pages.length] as KioskBoard;
	}

	/**
	 * Advance the clock.
	 *
	 * @param now - Milliseconds since the epoch.
	 * @param visible - Whether the page was visible since the last update.
	 * @returns True when the shown dashboard changed.
	 */
	update(now: number, visible: boolean): boolean {
		const since = this.#last === null ? 0 : Math.max(now - this.#last, 0);
		this.#last = now;
		if (!visible) return false; // hidden time does not count: rotation pauses
		this.#shownMs += since;
		if (this.#shownMs < this.dwellMs) return false;
		this.#shownMs = 0; // one step per update, so a long pause never skips dashboards
		this.#index = (this.#index + 1) % this.pages.length;
		return true;
	}

	/**
	 * Tell whether the next dashboard's data should be fetched now, just before the switch.
	 *
	 * @returns True within `PREFETCH_MS` of the switch.
	 */
	prefetchDue(): boolean {
		return this.dwellMs - this.#shownMs <= PREFETCH_MS;
	}

	/**
	 * Describe where the rotation is.
	 *
	 * @returns The position line's facts.
	 */
	position(): RotationPosition {
		const left = Math.max(this.dwellMs - this.#shownMs, 0);
		return {
			current: this.#index + 1,
			total: this.pages.length,
			next: this.next,
			nextInS: Math.ceil(left / SECOND)
		};
	}
}
