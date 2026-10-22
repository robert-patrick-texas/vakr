/**
 * The dashboard editor's model: drafts, widget defaults, bounds, and pinning (UI-004, E.31).
 *
 * A draft is the API's own body shape, so what the editor saves is what it
 * shows. `problems` mirrors the E.31 bounds the API enforces, so the editor
 * can say what is missing before saving; the API checks everything again,
 * including that every pinned interface and filter target exists.
 * `pinInto` adds one interface to a dashboard for the interface page's
 * "Pin to dashboard": into the first pinned widget with room, else a new
 * one.
 *
 * @example
 * ```ts
 * const draft = newDraft();
 * draft.widgets.push(emptyWidget('device_selection'));
 * problems(draft); // ['Widget 1 (Devices): choose at least one filter.']
 * ```
 *
 * @packageDocumentation
 */
import type {
	DashboardCreate,
	DashboardOut,
	DashboardPatch,
	DeviceCriteriaIn,
	InterfaceCriteriaIn,
	WidgetIn
} from '#lib/api/types.ts';

/** Appendix E.31 bounds, as the API enforces them. */
export const LIMITS = {
	name: 80,
	description: 500,
	title: 80,
	widgets: 12,
	rankings: 2,
	pinned: 50,
	selectionRows: 100,
	rankingRows: 25,
	refreshMin: 15,
	refreshMax: 3600
} as const;

/** Refresh choices offered by the editor, seconds. */
export const REFRESH_CHOICES = [15, 30, 60, 120, 300, 600, 1800, 3600] as const;

export type WidgetKind = WidgetIn['kind'];

/** Each kind's name in the editor, and the default title of a new widget. */
export const KIND_NAMES: Record<WidgetKind, string> = {
	pinned_interfaces: 'Pinned interfaces',
	interface_selection: 'Interfaces',
	device_selection: 'Devices',
	ranking: 'Ranking'
};

/** What the editor holds: the create body with every field present. */
export interface Draft {
	name: string;
	description: string;
	refresh_s: number;
	widgets: WidgetIn[];
}

/**
 * A new, empty dashboard.
 *
 * @returns The draft.
 */
export function newDraft(): Draft {
	return { name: '', description: '', refresh_s: 60, widgets: [] };
}

/**
 * Empty interface criteria.
 *
 * @returns Criteria that filter nothing yet.
 */
export function noInterfaceFilter(): InterfaceCriteriaIn {
	return { wan_only: false };
}

/**
 * A new widget of one kind, with the E.31 defaults.
 *
 * @param kind - The kind.
 * @returns The widget.
 */
export function emptyWidget(kind: WidgetKind): WidgetIn {
	const title = KIND_NAMES[kind];
	switch (kind) {
		case 'pinned_interfaces':
			return { kind, title, interfaces: [], graphs: true };
		case 'interface_selection':
			return { kind, title, criteria: noInterfaceFilter(), limit: 25 };
		case 'device_selection':
			return { kind, title, criteria: {}, limit: 25 };
		case 'ranking':
			return {
				kind,
				title,
				ranking: 'utilization',
				criteria: noInterfaceFilter(),
				window: '15m',
				limit: 10
			};
	}
}

/**
 * Start editing a stored dashboard.
 *
 * @param d - The definition.
 * @returns A draft holding a deep copy of it.
 */
export function fromDefinition(d: DashboardOut): Draft {
	const widgets = JSON.parse(JSON.stringify(d.widgets)) as WidgetIn[];
	return { name: d.name, description: d.description, refresh_s: d.refresh_s, widgets };
}

/**
 * Tell whether criteria filter anything.
 *
 * @param c - Interface or device criteria.
 * @returns True when at least one filter is set.
 */
export function filters(c: InterfaceCriteriaIn | DeviceCriteriaIn): boolean {
	const i = c as InterfaceCriteriaIn;
	return Boolean(c.site_id || c.tag_id || c.group_id || i.provider || i.wan_only || i.interest);
}

/**
 * Say what is wrong with one widget, if anything.
 *
 * @param w - The widget.
 * @returns The problem, or null.
 */
function widgetProblem(w: WidgetIn): string | null {
	if (w.title.trim() === '') return 'give it a title.';
	if (w.kind === 'pinned_interfaces' && w.interfaces.length === 0) {
		return 'pin at least one interface.';
	}
	if ((w.kind === 'interface_selection' || w.kind === 'device_selection') && !filters(w.criteria)) {
		return 'choose at least one filter.';
	}
	return null;
}

/**
 * List what must change before the draft can be saved.
 *
 * @param d - The draft.
 * @returns One sentence per problem; empty when it can be saved.
 */
export function problems(d: Draft): string[] {
	const found: string[] = [];
	if (d.name.trim() === '') found.push('Give the dashboard a name.');
	if (d.widgets.length > LIMITS.widgets) found.push(`At most ${LIMITS.widgets} widgets.`);
	if (d.widgets.filter((w) => w.kind === 'ranking').length > LIMITS.rankings) {
		found.push(`At most ${LIMITS.rankings} ranking widgets.`);
	}
	d.widgets.forEach((w, n) => {
		const problem = widgetProblem(w);
		if (problem)
			found.push(`Widget ${n + 1} (${w.title.trim() || KIND_NAMES[w.kind]}): ${problem}`);
	});
	return found;
}

/**
 * The create body of a draft, names trimmed.
 *
 * @param d - The draft.
 * @returns `POST /api/v1/dashboards` body.
 */
export function toCreate(d: Draft): DashboardCreate {
	const widgets = d.widgets.map((w) => ({ ...w, title: w.title.trim() }));
	return {
		name: d.name.trim(),
		description: d.description.trim(),
		refresh_s: d.refresh_s,
		widgets
	};
}

/**
 * The patch body of a draft: everything, based on the version the editor loaded.
 *
 * @param d - The draft.
 * @param version - The definition's version when the editor opened it (DB-003).
 * @returns `PATCH /api/v1/dashboards/{uuid}` body.
 */
export function toPatch(d: Draft, version: number): DashboardPatch {
	return { version, ...toCreate(d) };
}

/**
 * Move one item of a list up or down.
 *
 * @param list - The list.
 * @param index - The item.
 * @param delta - -1 for up, +1 for down.
 * @returns A new list; the same order when the move would leave the list.
 */
export function move<T>(list: readonly T[], index: number, delta: number): T[] {
	const to = index + delta;
	const out = [...list];
	if (to < 0 || to >= list.length || index < 0 || index >= list.length) return out;
	const [item] = out.splice(index, 1) as [T];
	out.splice(to, 0, item);
	return out;
}

/**
 * Add an interface to a dashboard, for "Pin to dashboard" on the interface page.
 *
 * @param d - The definition as stored now.
 * @param interfaceId - The interface to pin.
 * @returns The patch to send, or why it cannot be pinned there.
 */
export function pinInto(
	d: DashboardOut,
	interfaceId: string
): { body: DashboardPatch } | { error: string } {
	const widgets = JSON.parse(JSON.stringify(d.widgets)) as WidgetIn[];
	const pinned = widgets.filter((w) => w.kind === 'pinned_interfaces');
	if (pinned.some((w) => w.interfaces.includes(interfaceId))) {
		return { error: `It is already pinned on ${d.name}.` };
	}
	const room = pinned.find((w) => w.interfaces.length < LIMITS.pinned);
	if (room) room.interfaces.push(interfaceId);
	else if (widgets.length < LIMITS.widgets) {
		widgets.push({ ...emptyWidget('pinned_interfaces'), interfaces: [interfaceId] } as WidgetIn);
	} else {
		return { error: `${d.name} has no room: ${LIMITS.widgets} widgets, every pinned one full.` };
	}
	return { body: { version: d.version, widgets } };
}
