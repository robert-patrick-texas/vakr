/**
 * Operation words match the CLI's and never hide a failure's reason (API-011, DISC-T03).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { isActive, outcomeText, resultText } from './words.ts';

const RESULT = {
	interfaces: 24,
	new: 1,
	renumbered: 0,
	missing: 2,
	reviews_opened: 1,
	capability_changes: 0,
	partial: false,
	duration_ms: 1800
};

describe('operation words', () => {
	it('lists the counts in the CLI’s order with the duration', () => {
		expect(resultText(RESULT)).toBe(
			'24 interfaces, 1 new, 0 renumbered, 2 missing, 1 identity reviews opened in 1.8 s'
		);
		expect(resultText({ ...RESULT, capability_changes: 3, partial: true })).toContain(
			', 3 capability changes (partial: some table came back incomplete)'
		);
	});
	it('says where an active operation is and why a finished one failed', () => {
		expect(outcomeText({ state: 'queued', result: null, error: null })).toMatch(/^Queued/);
		expect(outcomeText({ state: 'running', result: null, error: null })).toMatch(/^Running/);
		expect(outcomeText({ state: 'succeeded', result: RESULT, error: null })).toMatch(
			/^Succeeded: 24 interfaces/
		);
		expect(outcomeText({ state: 'expired', result: null, error: 'no collector' })).toBe(
			'Expired: no collector'
		);
		expect(isActive({ state: 'running' })).toBe(true);
		expect(isActive({ state: 'failed' })).toBe(false);
	});
});
