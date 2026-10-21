/**
 * The cursors a list page came through, so Previous and First work (API-005).
 *
 * API v1 pages forward only: each answer names `page.next_cursor`. A
 * `CursorTrail` remembers the cursors behind the current one. Changing a
 * filter or the sort starts again at the first page (`reset`).
 *
 * @example
 * ```ts
 * const trail = new CursorTrail();
 * trail.next('opaque-cursor');
 * trail.number; // 2
 * ```
 *
 * @packageDocumentation
 */

/** Cursor history of one list. */
export class CursorTrail {
	/** The cursor of the page shown; `null` for the first page. */
	cursor = $state<string | null>(null);
	#behind = $state<(string | null)[]>([]);

	/**
	 * The page number shown.
	 *
	 * @returns 1 for the first page.
	 */
	get number(): number {
		return this.#behind.length + 1;
	}

	/**
	 * Go to the page after the current one.
	 *
	 * @param cursor - `page.next_cursor` of the current answer.
	 */
	next = (cursor: string): void => {
		this.#behind = [...this.#behind, this.cursor];
		this.cursor = cursor;
	};

	/** Go back one page. */
	previous = (): void => {
		const behind = [...this.#behind];
		this.cursor = behind.pop() ?? null;
		this.#behind = behind;
	};

	/** Go to the first page (also after a filter or sort change). */
	reset = (): void => {
		this.#behind = [];
		this.cursor = null;
	};
}
