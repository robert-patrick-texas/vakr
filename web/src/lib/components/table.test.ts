/**
 * Sortable headers say their state; the status timeline colours states (12.8, UI-011, 12.5).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { ariaSort, type Column } from './table.ts';
import { stateColor, timelineItems, type TimelineStyle } from '#lib/charts/echarts-timeline.ts';
import { MARKER_LETTER, withAlpha } from '#lib/charts/uplot-options.ts';

const cell = (() => null) as unknown as Column<unknown>['cell'];

describe('12.8: aria-sort on sortable headers', () => {
	it('marks the active column with its direction and others as none', () => {
		const name: Column<unknown> = { id: 'n', header: 'Name', cell, sortKey: 'name' };
		const age: Column<unknown> = {
			id: 'a',
			header: 'Age',
			cell,
			sortKey: 'age',
			sortDirection: 'descending'
		};
		const plain: Column<unknown> = { id: 'p', header: 'Plain', cell };
		expect(ariaSort(name, 'name')).toBe('ascending');
		expect(ariaSort(age, 'age')).toBe('descending');
		expect(ariaSort(age, 'name')).toBe('none');
		expect(ariaSort(plain, 'name')).toBeUndefined();
	});
});

describe('12.5: state timeline items', () => {
	const style: TimelineStyle = {
		up: 'green',
		down: 'red',
		degraded: 'amber',
		unknown: 'gray',
		text: 'black',
		grid: 'silver',
		formatTime: String
	};

	it('makes an admin and an oper row per segment, with "no data" for a gap', () => {
		const items = timelineItems([
			{
				start: '2026-10-20T11:00:00Z',
				end: '2026-10-20T11:20:00Z',
				admin: null,
				oper: null,
				samples: 0
			},
			{
				start: '2026-10-20T11:20:00Z',
				end: '2026-10-20T12:00:00Z',
				admin: 'up',
				oper: 'down',
				samples: 40
			}
		]);
		expect(items.map((i) => [i[0], i[3]])).toEqual([
			[0, 'no data'],
			[1, 'no data'],
			[0, 'up'],
			[1, 'down']
		]);
	});

	it('never colours missing data as up', () => {
		expect(stateColor(null, style)).toBe('gray');
		expect(stateColor('up', style)).toBe('green');
		expect(stateColor('lower-layer-down', style)).toBe('red');
		expect(stateColor('dormant', style)).toBe('amber');
	});
});

describe('UI-010: marker letters and fills', () => {
	it('has a letter for every marker kind', () => {
		expect(Object.keys(MARKER_LETTER).sort()).toEqual(
			[
				'baseline',
				'collector',
				'discontinuity',
				'gap',
				'over_capacity',
				'partial',
				'restart'
			].sort()
		);
		expect(withAlpha('#1f5fae', 0.5)).toBe('rgba(31, 95, 174, 0.5)');
		expect(withAlpha('red', 0.5)).toBe('red');
	});
});
