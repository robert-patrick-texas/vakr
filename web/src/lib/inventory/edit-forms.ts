/**
 * Drafts, change bodies, and refusals of the manual inventory forms (27.4, META-008, DB-003).
 *
 * A draft is what the form's inputs hold: text, with lists typed as
 * comma-separated names. A create body leaves blank fields out; a change
 * body (`PATCH`) carries the version the form opened and only the fields
 * the admin changed, with `null` for a field emptied, so a field nobody
 * touched never counts as a change to a record a file manages.
 *
 * `refusalOf` turns an API refusal into the form's words: one summary, and
 * a message per field taken from the envelope's `fields` (`body.address`):
 * the file's rules (422), a field `inventory.toml` manages (409
 * `managed_by_file`, naming the file), someone else's newer save (409
 * `conflict`), and a peer node's refusal naming its primary (409
 * `peer_read_only`, MPOS-003).
 *
 * @example
 * ```ts
 * toDevicePatch({ ...draft, comment: 'lab' }, draft, 3); // { version: 3, comment: 'lab' }
 * ```
 *
 * @packageDocumentation
 */
import { ApiFailure, describeFailure } from '#lib/api/errors.ts';
import type {
	AnnotationOut,
	AnnotationPatch,
	DeviceCreate,
	DevicePatch,
	DeviceRecordOut
} from '#lib/api/types.ts';

export const DEVICE_FIELDS = [
	'name',
	'address',
	'site',
	'credential',
	'tags',
	'groups',
	'admin_state',
	'interval',
	'discovery_interval',
	'reachability',
	'address_family',
	'comment'
] as const;
export type DeviceField = (typeof DEVICE_FIELDS)[number];
/** The device form's inputs, all text. */
export type DeviceDraft = Record<DeviceField, string>;
const LISTS = new Set<string>(['tags', 'groups']);

/** The annotation form's inputs. */
export interface AnnotationDraft {
	interest: 'primary' | 'secondary';
	sticky: boolean;
	wan_circuit: boolean;
	provider: string;
	circuit_id: string;
	contracted_bps: string;
	capacity_override_bps: string;
	tags: string;
	comment: string;
}

/** What a refused save means for the form. */
export interface Refusal {
	summary: string;
	fields: Record<string, string>;
	conflict: boolean;
}

/**
 * A blank device: enabled, everything else unset.
 *
 * @returns The draft.
 */
export function emptyDevice(): DeviceDraft {
	const draft = Object.fromEntries(DEVICE_FIELDS.map((f) => [f, ''])) as DeviceDraft;
	draft.admin_state = 'enabled';
	return draft;
}

/**
 * The draft of a stored device.
 *
 * @param r - The record.
 * @returns The draft.
 */
export function deviceDraft(r: DeviceRecordOut): DeviceDraft {
	return {
		name: r.name,
		address: r.address,
		site: r.site ?? '',
		credential: r.credential ?? '',
		tags: r.tags.join(', '),
		groups: r.groups.join(', '),
		admin_state: r.admin_state,
		interval: r.interval ?? '',
		discovery_interval: r.discovery_interval ?? '',
		reachability: r.reachability ?? '',
		address_family: r.address_family ?? '',
		comment: r.comment
	};
}

/**
 * Split a comma-separated list of names.
 *
 * @param text - Such as `wan, edge`.
 * @returns The names, trimmed, without blanks.
 */
export function names(text: string): string[] {
	return text
		.split(',')
		.map((n) => n.trim())
		.filter((n) => n !== '');
}

/**
 * The value a field sends: lists as arrays, blank as `null` (the comment as `''`).
 *
 * @param field - The field.
 * @param text - Its input.
 * @returns The JSON value.
 */
function sent(field: string, text: string): string | string[] | null {
	if (LISTS.has(field)) return names(text);
	const trimmed = text.trim();
	if (field === 'comment') return trimmed;
	return trimmed === '' ? null : trimmed;
}

/**
 * The body that creates a device: blank fields are left out.
 *
 * @param d - The draft.
 * @returns The `POST /api/v1/devices` body.
 */
export function toDeviceCreate(d: DeviceDraft): DeviceCreate {
	const body: Record<string, unknown> = {};
	for (const field of DEVICE_FIELDS) {
		const value = sent(field, d[field]);
		if (value !== null && !(Array.isArray(value) && value.length === 0) && value !== '') {
			body[field] = value;
		}
	}
	return { name: d.name.trim(), address: d.address.trim(), ...body } as DeviceCreate;
}

/**
 * The body that changes a device: the version and every field the admin changed.
 *
 * @param d - The draft now.
 * @param before - The draft as the form opened it.
 * @param version - The version the form opened (DB-003).
 * @returns The `PATCH` body.
 */
export function toDevicePatch(d: DeviceDraft, before: DeviceDraft, version: number): DevicePatch {
	const body: Record<string, unknown> = { version };
	for (const field of DEVICE_FIELDS) {
		const now = sent(field, d[field]);
		if (JSON.stringify(now) !== JSON.stringify(sent(field, before[field]))) body[field] = now;
	}
	return body as DevicePatch;
}

/**
 * The draft of a stored annotation.
 *
 * @param a - The annotation.
 * @returns The draft.
 */
export function annotationDraft(a: AnnotationOut): AnnotationDraft {
	return {
		interest: a.interest,
		sticky: a.sticky,
		wan_circuit: a.wan_circuit,
		provider: a.provider ?? '',
		circuit_id: a.circuit_id ?? '',
		contracted_bps: a.contracted_bps === null ? '' : String(a.contracted_bps),
		capacity_override_bps: a.capacity_override_bps === null ? '' : String(a.capacity_override_bps),
		tags: a.tags.join(', '),
		comment: a.comment
	};
}

/**
 * Check a rate input before sending it.
 *
 * @param text - Bits per second, or blank.
 * @returns Why it is not a rate, or `null`.
 */
export function rateProblem(text: string): string | null {
	const t = text.trim();
	return t === '' || /^[1-9]\d{0,18}$/.test(t) ? null : 'Whole bits per second, at least 1.';
}

/**
 * The body that changes an annotation: the version and every field the admin changed.
 *
 * @param d - The draft now.
 * @param before - The draft as the form opened it.
 * @param version - The version the form opened (DB-003).
 * @returns The `PATCH` body.
 */
export function toAnnotationPatch(
	d: AnnotationDraft,
	before: AnnotationDraft,
	version: number
): AnnotationPatch {
	const value = (key: keyof AnnotationDraft, draft: AnnotationDraft): unknown => {
		const v = draft[key];
		if (typeof v !== 'string' || key === 'interest') return v;
		if (key === 'tags') return names(v);
		if (key === 'comment') return v.trim();
		if (key.endsWith('_bps')) return v.trim() === '' ? null : Number(v.trim());
		return v.trim() === '' ? null : v.trim();
	};
	const body: Record<string, unknown> = { version };
	for (const key of Object.keys(d) as (keyof AnnotationDraft)[]) {
		const now = value(key, d);
		if (JSON.stringify(now) !== JSON.stringify(value(key, before))) body[key] = now;
	}
	return body as AnnotationPatch;
}

/**
 * Explain a refused save in the form's words.
 *
 * @param error - What the request threw.
 * @param noun - What the form edits, such as `device`.
 * @returns The summary and a message per field.
 */
export function refusalOf(error: unknown, noun: string): Refusal {
	if (!(error instanceof ApiFailure))
		return { summary: 'Saving failed.', fields: {}, conflict: false };
	const fields: Record<string, string> = {};
	for (const f of error.fields) fields[f.field.replace(/^body\./, '')] = f.message;
	if (error.code === 'managed_by_file') {
		for (const [key, text] of Object.entries(fields)) {
			fields[key] = `${text.charAt(0).toUpperCase()}${text.slice(1)}: change it there.`;
		}
		return { summary: error.message, fields, conflict: false };
	}
	if (error.code === 'conflict' && /changed by someone else/.test(error.message)) {
		const summary = `Someone changed this ${noun} since you opened it. Reload to see their version, then apply your change again.`;
		return { summary, fields: {}, conflict: true };
	}
	if (error.code === 'peer_read_only' || error.kind === 'conflict') {
		return { summary: `Not saved: ${error.message}`, fields, conflict: false };
	}
	if (error.kind === 'invalid') {
		const summary = Object.keys(fields).length
			? 'Not saved: correct the fields marked below.'
			: `Not saved: ${error.message}`;
		return { summary, fields, conflict: false };
	}
	if (error.kind === 'forbidden') {
		return { summary: `Only an admin can change this ${noun}.`, fields: {}, conflict: false };
	}
	return { summary: describeFailure(error).detail, fields: {}, conflict: false };
}
