/**
 * The dashboard editor's model: defaults, the E.31 bounds, ordering, and pinning (UI-004).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import type { DashboardOut, WidgetIn } from '#lib/api/types.ts';
import {
	LIMITS,
	emptyWidget,
	fromDefinition,
	move,
	newDraft,
	pinInto,
	problems,
	toCreate,
	toPatch
} from './editor.ts';

const IFACE = '0190a3c4-0000-7000-8000-00000000aaaa';
const SITE = '0190a3c4-0000-7000-8000-00000000bbbb';

/**
 * A stored definition with the given widgets.
 *
 * @param widgets - Its widgets.
 * @returns The definition.
 */
function stored(widgets: WidgetIn[]): DashboardOut {
	return {
		id: '0190a3c4-0000-7000-8000-00000000cccc',
		name: 'NOC',
		description: '',
		refresh_s: 60,
		widgets,
		widget_schema: 1,
		managed_by: 'api',
		visibility: 'private',
		created_by: 'adam',
		created_at: '2026-10-21T12:00:00Z',
		updated_by: 'adam',
		updated_at: '2026-10-21T12:00:00Z',
		version: 3
	};
}

describe('problems (E.31 bounds before saving)', () => {
	it('needs a name, and each widget its interfaces or a filter', () => {
		const draft = newDraft();
		draft.widgets.push(emptyWidget('pinned_interfaces'), emptyWidget('device_selection'));
		expect(problems(draft)).toEqual([
			'Give the dashboard a name.',
			'Widget 1 (Pinned interfaces): pin at least one interface.',
			'Widget 2 (Devices): choose at least one filter.'
		]);
	});
	it('accepts a complete draft; a ranking needs no filter', () => {
		const draft = { ...newDraft(), name: 'NOC' };
		const chosen = emptyWidget('interface_selection');
		if (chosen.kind === 'interface_selection') chosen.criteria.site_id = SITE;
		draft.widgets.push(chosen, emptyWidget('ranking'));
		expect(problems(draft)).toEqual([]);
	});
	it('counts widgets and rankings against their limits', () => {
		const draft = { ...newDraft(), name: 'NOC' };
		draft.widgets = Array.from({ length: LIMITS.widgets + 1 }, () => emptyWidget('ranking'));
		expect(problems(draft)).toContain(`At most ${LIMITS.widgets} widgets.`);
		expect(problems(draft)).toContain(`At most ${LIMITS.rankings} ranking widgets.`);
	});
});

describe('bodies', () => {
	it('trims names and titles, and a patch carries the version it was based on', () => {
		const top = emptyWidget('ranking');
		top.title = ' Top ';
		const draft = { ...newDraft(), name: ' NOC ', widgets: [top] };
		expect(toCreate(draft)).toMatchObject({ name: 'NOC', widgets: [{ title: 'Top' }] });
		expect(toPatch(draft, 4)).toMatchObject({ version: 4, name: 'NOC' });
	});
	it('edits a deep copy of the stored definition', () => {
		const definition = stored([
			{ ...emptyWidget('pinned_interfaces'), interfaces: [IFACE] } as WidgetIn
		]);
		const draft = fromDefinition(definition);
		const first = draft.widgets[0];
		if (first?.kind === 'pinned_interfaces') first.interfaces.push('x');
		expect(definition.widgets[0]).toMatchObject({ interfaces: [IFACE] });
	});
});

describe('move', () => {
	it('moves within the list and leaves it unchanged at the ends', () => {
		expect(move(['a', 'b', 'c'], 0, 1)).toEqual(['b', 'a', 'c']);
		expect(move(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b']);
		expect(move(['a', 'b'], 0, -1)).toEqual(['a', 'b']);
		expect(move(['a', 'b'], 1, 1)).toEqual(['a', 'b']);
	});
});

describe('pinInto (Pin to dashboard)', () => {
	const full = {
		...emptyWidget('pinned_interfaces'),
		interfaces: Array.from({ length: 50 }, (_, n) => `i${n}`)
	};
	it('adds to the first pinned widget with room, with the stored version', () => {
		const result = pinInto(
			stored([{ ...emptyWidget('pinned_interfaces'), interfaces: ['a'] } as WidgetIn]),
			IFACE
		);
		expect(result).toEqual({
			body: { version: 3, widgets: [expect.objectContaining({ interfaces: ['a', IFACE] })] }
		});
	});
	it('adds a new pinned widget when the others are full', () => {
		const result = pinInto(stored([full as WidgetIn]), IFACE);
		expect('body' in result && result.body.widgets?.[1]).toMatchObject({ interfaces: [IFACE] });
	});
	it('refuses a second pin, and a dashboard without room', () => {
		const once = stored([{ ...emptyWidget('pinned_interfaces'), interfaces: [IFACE] } as WidgetIn]);
		expect(pinInto(once, IFACE)).toEqual({ error: 'It is already pinned on NOC.' });
		const packed = stored(Array.from({ length: LIMITS.widgets }, () => full as WidgetIn));
		expect(pinInto(packed, IFACE)).toHaveProperty('error');
	});
});
