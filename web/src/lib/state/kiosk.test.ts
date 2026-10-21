/**
 * Kiosk options and the rotation schedule (UI-013, DASH-T06; Appendix E.31).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import {
	DWELL_DEFAULT_S,
	PREFETCH_MS,
	REFRESH_MIN_S,
	RotationSchedule,
	kioskOptions,
	rotationOptions
} from './kiosk.ts';

const q = (text: string) => new URLSearchParams(text);

describe('kioskOptions (DASH-T06: refresh never below the minimum)', () => {
	it('defaults to each dashboard’s own interval and the 15 m window', () => {
		expect(kioskOptions(q(''))).toEqual({ refreshMs: null, window: '15m' });
	});
	it('takes refresh within 15 s to 1 h and a known window', () => {
		expect(kioskOptions(q('refresh=15&window=24h'))).toEqual({ refreshMs: 15_000, window: '24h' });
		expect(kioskOptions(q('refresh=3600'))).toEqual({ refreshMs: 3_600_000, window: '15m' });
	});
	it.each(['14', '0', '3601', '-5', '1.5', 'fast', '', '9999999'])(
		'refuses refresh=%s with the bounds in words',
		(value) => {
			const result = kioskOptions(q(`refresh=${value}`));
			expect(result).toEqual({ error: expect.stringContaining(`from ${REFRESH_MIN_S} to 3600`) });
		}
	);
	it('refuses an unknown window', () => {
		expect(kioskOptions(q('window=7d'))).toHaveProperty('error');
	});
});

describe('rotationOptions', () => {
	it('reads pages in order with repeats, and every with its default', () => {
		expect(rotationOptions(q('pages=overview,wan,overview'))).toEqual({
			pages: ['overview', 'wan', 'overview'],
			dwellMs: DWELL_DEFAULT_S * 1000
		});
		expect(rotationOptions(q('pages=wan,rankings&every=15'))).toEqual({
			pages: ['wan', 'rankings'],
			dwellMs: 15_000
		});
	});
	it('names an unknown dashboard instead of skipping it', () => {
		const result = rotationOptions(q('pages=overview,weather'));
		expect(result).toEqual({
			error: expect.stringContaining('no kiosk dashboard named "weather"')
		});
	});
	it.each(['', 'pages=overview', 'pages=overview,,wan', `pages=${Array(9).fill('wan').join(',')}`])(
		'refuses %s: 2 to 8 entries, no empty ones',
		(text) => {
			expect(rotationOptions(q(text))).toHaveProperty('error');
		}
	);
	it.each(['14', '3601', 'x'])('refuses every=%s', (value) => {
		expect(rotationOptions(q(`pages=overview,wan&every=${value}`))).toHaveProperty('error');
	});
});

describe('RotationSchedule (UI-013)', () => {
	it('switches at the dwell time, wraps around, and says what comes next', () => {
		const s = new RotationSchedule(['overview', 'wan', 'rankings'], 60_000);
		s.update(0, true);
		expect(s.board).toBe('overview');
		expect(s.position()).toEqual({ current: 1, total: 3, next: 'wan', nextInS: 60 });
		expect(s.update(20_000, true)).toBe(false);
		expect(s.position().nextInS).toBe(40);
		expect(s.update(60_000, true)).toBe(true);
		expect(s.board).toBe('wan');
		expect(s.update(120_000, true)).toBe(true);
		expect(s.update(180_000, true)).toBe(true);
		expect(s.board).toBe('overview');
	});
	it('pauses while hidden: hidden time never counts', () => {
		const s = new RotationSchedule(['overview', 'wan'], 30_000);
		s.update(0, true);
		s.update(20_000, true);
		expect(s.update(500_000, false)).toBe(false);
		expect(s.position().nextInS).toBe(10);
		expect(s.update(505_000, true)).toBe(false);
		expect(s.update(510_000, true)).toBe(true);
	});
	it('moves one step per update, so a long stall never skips a dashboard', () => {
		const s = new RotationSchedule(['overview', 'wan', 'rankings'], 15_000);
		s.update(0, true);
		expect(s.update(1_000_000, true)).toBe(true);
		expect(s.board).toBe('wan');
	});
	it('says when to read the next dashboard ahead: within 5 s of the switch', () => {
		const s = new RotationSchedule(['overview', 'wan'], 60_000);
		s.update(0, true);
		s.update(60_000 - PREFETCH_MS - 1, true);
		expect(s.prefetchDue()).toBe(false);
		s.update(60_000 - PREFETCH_MS, true);
		expect(s.prefetchDue()).toBe(true);
	});
});
