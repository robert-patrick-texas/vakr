/**
 * Kiosk dashboards make one or two bounded reads per refresh (DASH-T06, DASH-010, UI-013).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { REFRESH } from '#lib/query/policy.ts';
import { KIOSK_BOARDS } from '#lib/state/kiosk.ts';
import { boardReads } from './boards.ts';
import { EXPIRY_WARNING_MS, expiresSoon } from './expiry.ts';

describe('boardReads (DASH-T06)', () => {
	it('makes at most two reads per refresh, each a bounded page', () => {
		const counts = KIOSK_BOARDS.map(
			(b) => boardReads(b, { refreshMs: null, window: '15m' }).length
		);
		expect(counts).toEqual([1, 1, 1, 2]);
	});
	it('keeps each dashboard’s own interval unless the kiosk sets one', () => {
		const own = (b: (typeof KIOSK_BOARDS)[number]) =>
			boardReads(b, { refreshMs: null, window: '1h' })[0]?.refetchInterval;
		expect(own('overview')).toBe(REFRESH.list);
		expect(own('rankings')).toBe(REFRESH.ranking);
		const set = boardReads('rankings', { refreshMs: 120_000, window: '1h' });
		expect(set.map((r) => r.refetchInterval)).toEqual([120_000, 120_000]);
	});
	it('asks the rankings for ten rows of the kiosk window', () => {
		const [util] = boardReads('rankings', { refreshMs: null, window: '6h' });
		expect(util?.queryKey).toEqual(['ranking', 'utilization', { window: '6h', limit: 10 }]);
	});
});

describe('expiresSoon', () => {
	const now = Date.parse('2026-10-21T00:00:00Z');
	it('warns within seven days of the expiry', () => {
		expect(expiresSoon(new Date(now + EXPIRY_WARNING_MS).toISOString(), now)).not.toBeNull();
		expect(expiresSoon(new Date(now + EXPIRY_WARNING_MS + 1000).toISOString(), now)).toBeNull();
	});
	it('says nothing without a known expiry', () => {
		expect(expiresSoon(null, now)).toBeNull();
		expect(expiresSoon('not a time', now)).toBeNull();
	});
});
