/**
 * Every page maps its data to one UI-014 state and never implies certainty (UI-014, UI-015, WEB-T07).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { ApiFailure, describeFailure, isRetryable, kindOf } from '#lib/api/errors.ts';
import type { HealthView, Reading } from '#lib/api/types.ts';
import { completenessNotes, healthNotes, pageState } from './page-state.ts';

const ok: Reading = { text: 'ok', value: 'ok', reason: null, stale: false };
const query = <T>(data: T | undefined, error: unknown = null) => ({
	data,
	error,
	isPending: data === undefined && error === null,
	dataUpdatedAt: Date.UTC(2026, 9, 20, 12, 0)
});

describe('UI-014: pageState', () => {
	it('is loading before the first answer', () => {
		expect(pageState(query(undefined)).kind).toBe('loading');
	});

	it.each([
		[401, 'signed-out'],
		[403, 'denied'],
		[404, 'not-found'],
		[503, 'unavailable'],
		[502, 'unavailable'],
		[422, 'failed']
	])('maps HTTP %i without data to %s', (status, kind) => {
		const error = new ApiFailure(kindOf(status), status, { code: 'x', message: 'm' });
		expect(pageState(query(undefined, error)).kind).toBe(kind);
	});

	it('treats no answer at all as the backend being unavailable, not the devices', () => {
		const error = new ApiFailure('unavailable', 0, { code: 'no_answer', message: 'down' });
		const state = pageState(query(undefined, error));
		expect(state.kind).toBe('unavailable');
		if (state.kind !== 'ready') expect(state.detail).toContain('says nothing about the devices');
	});

	it('keeps shown data after a failed refresh and says it is stale (UI-015)', () => {
		const error = new ApiFailure('unavailable', 503, { code: 'busy', message: 'busy' });
		const state = pageState(query({ rows: [1] }, error));
		expect(state.kind).toBe('ready');
		if (state.kind === 'ready') {
			expect(state.stale).toContain('The last refresh failed');
			expect(state.data.rows).toEqual([1]);
		}
	});

	it('reports empty, partial, and evidence staleness from the page rules', () => {
		const state = pageState(query({ rows: [] as number[] }), {
			isEmpty: (d) => d.rows.length === 0,
			partial: () => ['Telemetry unavailable'],
			stale: () => 'Evidence is old.'
		});
		expect(state).toMatchObject({ kind: 'ready', empty: true, partial: ['Telemetry unavailable'] });
		if (state.kind === 'ready') expect(state.stale).toBe('Evidence is old.');
	});
});

describe('UI-015, invariant 2: healthNotes and completeness', () => {
	it('says nothing when every part is healthy', () => {
		const health: HealthView = { metadata: ok, telemetry: ok, collectors: [] };
		expect(healthNotes(health)).toEqual([]);
	});

	it('names telemetry trouble as Unknown, never Down', () => {
		const down: Reading = {
			text: 'unavailable: x',
			value: 'unavailable',
			reason: 'no file',
			stale: false
		};
		const notes = healthNotes({ metadata: ok, telemetry: down, collectors: [] });
		expect(notes[0]).toContain('Unknown, not Down');
	});

	it('names an unhealthy or silent collector', () => {
		const silent: Reading = {
			text: '—',
			value: null,
			reason: 'no health report yet',
			stale: false
		};
		const notes = healthNotes({
			metadata: ok,
			telemetry: ok,
			collectors: [
				{ collector_id: 'c', position: 'west', state: silent, age: silent, spool: silent }
			]
		});
		expect(notes).toEqual([
			"Collector west: no health report yet; its devices' states are less certain."
		]);
	});

	it('notes unavailable and truncated answers', () => {
		expect(completenessNotes({ available: false, reason: 'no telemetry' }, 'graph')).toEqual([
			'Not available: no telemetry.'
		]);
		expect(completenessNotes({ truncated: true }, 'list')[0]).toContain('Only part of the list');
	});
});

describe('ADR-035 #6: which failures are retried', () => {
	it('retries only a backend outage', () => {
		expect(isRetryable(new ApiFailure('unavailable', 503, {}))).toBe(true);
		expect(isRetryable(new ApiFailure('forbidden', 403, {}))).toBe(false);
		expect(isRetryable(new ApiFailure('server', 500, {}))).toBe(false);
		expect(isRetryable(new Error('x'))).toBe(false);
	});

	it('describes failures with the request ID', () => {
		const f = new ApiFailure('forbidden', 403, {
			code: 'forbidden',
			message: 'm',
			request_id: 'r1'
		});
		expect(describeFailure(f)).toEqual({
			title: 'Permission denied',
			detail: 'Your role cannot do this (request r1).'
		});
	});
});
