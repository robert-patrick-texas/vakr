<!--
@component
Cursor paging controls for an API list (API-005, 12.9: server paging, never 100,000 rows).

The API answers `page.next_cursor` and `page.total`; the page keeps the
cursors it came through, so Previous and First work without the API
knowing about them. The position is announced when the user moves.

Example:
```svelte
<Pager page={answer.page} shown={answer.rows.length} pageNumber={pager.number}
  onNext={() => pager.next(answer.page.next_cursor)} onPrevious={pager.previous} onFirst={pager.first} />
```
-->
<script lang="ts">
	import type { PageInfo } from '#lib/api/types.ts';
	import { announce } from '#lib/a11y/announce.svelte.ts';

	interface Props {
		page: PageInfo;
		shown: number;
		pageNumber: number;
		onNext: (cursor: string) => void;
		onPrevious: () => void;
		onFirst: () => void;
		/** What the rows are, such as `devices`. */
		noun?: string;
	}

	const { page, shown, pageNumber, onNext, onPrevious, onFirst, noun = 'rows' }: Props = $props();
	const totalText = $derived(page.total === null ? 'an unknown number of' : String(page.total));

	/**
	 * Move to the next page and say where the user is.
	 */
	function next(): void {
		if (page.next_cursor === null) return;
		onNext(page.next_cursor);
		announce(`Page ${pageNumber + 1} of ${noun}`);
	}
</script>

<nav class="pager row" aria-label="Pages of {noun}">
	<span class="muted">Page {pageNumber}: {shown} of {totalText} {noun}</span>
	<button type="button" onclick={onFirst} disabled={pageNumber <= 1}>First</button>
	<button
		type="button"
		onclick={() => {
			onPrevious();
			announce(`Page ${pageNumber - 1} of ${noun}`);
		}}
		disabled={pageNumber <= 1}>Previous</button
	>
	<button type="button" onclick={next} disabled={page.next_cursor === null}>Next</button>
</nav>

<style>
	.pager {
		margin: 0.5rem 0;
	}
</style>
