/**
 * Unit tests of the manual inventory forms' drafts, bodies, and refusals (27.4, META-008, DB-003).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { ApiFailure } from '#lib/api/errors.ts';
import type { AnnotationOut, DeviceRecordOut } from '#lib/api/types.ts';
import {
	annotationDraft,
	deviceDraft,
	emptyDevice,
	rateProblem,
	refusalOf,
	toAnnotationPatch,
	toDeviceCreate,
	toDevicePatch
} from './edit-forms.ts';

const RECORD: DeviceRecordOut = {
	id: '0190a3c4-0000-7000-8000-00000000002a',
	name: 'edge9',
	address: '192.0.2.9',
	address_family: null,
	site: 'dal1',
	site_id: '0190a3c4-0000-7000-8000-00000000002b',
	credential: 'network-v2c',
	tags: ['edge', 'wan'],
	groups: [],
	comment: 'lab',
	admin_state: 'enabled',
	interval: '120s',
	discovery_interval: null,
	reachability: null,
	managed_by: 'api',
	managed_by_source: 'api',
	editable: true,
	created_at: '2026-10-27T00:00:00Z',
	updated_at: '2026-10-27T00:00:00Z',
	version: 3,
	changes: [],
	warnings: []
};

describe('27.4: device bodies', () => {
	it('creates with only the fields filled in', () => {
		const draft = { ...emptyDevice(), name: ' edge9 ', address: '192.0.2.9', tags: 'wan, ,edge' };
		expect(toDeviceCreate(draft)).toEqual({
			name: 'edge9',
			address: '192.0.2.9',
			tags: ['wan', 'edge'],
			admin_state: 'enabled'
		});
	});

	it('changes only what the admin changed, null for an emptied field (DB-003)', () => {
		const before = deviceDraft(RECORD);
		expect(toDevicePatch(before, before, 3)).toEqual({ version: 3 });
		const now = { ...before, interval: '', tags: 'wan', comment: ' lab ' };
		expect(toDevicePatch(now, before, 3)).toEqual({ version: 3, interval: null, tags: ['wan'] });
	});
});

describe('META-011, META-013: annotation bodies', () => {
	const stored: AnnotationOut = {
		id: '0190a3c4-0000-7000-8000-00000000003a',
		device_id: RECORD.id,
		device: 'edge9',
		if_name: 'Gi0/0/0',
		lifecycle: 'active',
		managed_by: 'api',
		managed_by_source: 'api',
		editable: true,
		interest: 'secondary',
		sticky: false,
		wan_circuit: false,
		provider: null,
		circuit_id: null,
		contracted_bps: null,
		capacity_override_bps: 1000000000,
		comment: '',
		tags: [],
		description_tags: ['isp'],
		classified: true,
		version: 2,
		updated_at: '2026-10-27T00:00:00Z',
		changes: []
	};

	it('sends the changed flags, rates as numbers, and vakr tags', () => {
		const before = annotationDraft(stored);
		const now = { ...before, interest: 'primary' as const, wan_circuit: true, tags: 'uplink' };
		now.contracted_bps = '100000000';
		now.capacity_override_bps = '';
		expect(toAnnotationPatch(now, before, 2)).toEqual({
			version: 2,
			interest: 'primary',
			wan_circuit: true,
			contracted_bps: 100000000,
			capacity_override_bps: null,
			tags: ['uplink']
		});
	});

	it('checks rates before sending', () => {
		expect(rateProblem('')).toBeNull();
		expect(rateProblem('1000')).toBeNull();
		expect(rateProblem('0')).not.toBeNull();
		expect(rateProblem('10 Mb/s')).not.toBeNull();
	});
});

describe('META-008, MPOS-003: refusals in the form’s words', () => {
	const envelope = (code: string, message: string, fields: string[] = []) => ({
		code,
		message,
		request_id: 'r1',
		fields: fields.map((field) => ({ field, message: 'managed by inventory.toml' }))
	});

	it('names each file-managed field and the file', () => {
		const r = refusalOf(
			new ApiFailure(
				'conflict',
				409,
				envelope('managed_by_file', 'device edge1 is managed by inventory.toml', ['body.address'])
			),
			'device'
		);
		expect(r.summary).toContain('inventory.toml');
		expect(r.fields['address']).toMatch(/^Managed by inventory\.toml/);
	});

	it('offers a reload for a stale version, and repeats a peer’s refusal', () => {
		const stale = envelope('conflict', 'device x was changed by someone else (edited version 1)');
		expect(refusalOf(new ApiFailure('conflict', 409, stale), 'device').conflict).toBe(true);
		const peer = envelope('peer_read_only', 'this node is a peer; its primary is https://east');
		expect(refusalOf(new ApiFailure('conflict', 409, peer), 'device').summary).toContain(
			'https://east'
		);
	});

	it('marks the fields of a 422 and survives anything else', () => {
		const bad = {
			...envelope('invalid_request', 'the change is not valid'),
			fields: [{ field: 'body.address', message: 'not an address' }]
		};
		const r = refusalOf(new ApiFailure('invalid', 422, bad), 'device');
		expect(r.fields).toEqual({ address: 'not an address' });
		expect(refusalOf(new Error('x'), 'device').summary).toBe('Saving failed.');
	});
});
