/**
 * URL state, ranges, units, links, and refresh policy (12.3, 12.6, E.28, E.31, ADR-035 #6).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { bitRate, count, percent } from '#lib/format/units.ts';
import { age, formatInstant, knownZone, zoneLabel } from '#lib/format/time.ts';
import { query, safeNext } from '#lib/links.ts';
import { MAX_RETRIES, retryDelay, shouldRetry } from '#lib/query/policy.ts';
import { ApiFailure } from '#lib/api/errors.ts';
import { readDeviceFilters, readInterfaceFilters, toQuery } from './filters.ts';
import { checkCustom, epochToWall, readRange, rangeParams, wallToEpoch } from './range.ts';

const UUID = '0190a3c4-0000-7000-8000-00000000002a';

describe('12.3: device and interface filters from the URL', () => {
	it('keeps only values the API accepts', () => {
		const f = readDeviceFilters(
			new URLSearchParams(`bucket=attention&bucket=bogus&sort=evil&site=${UUID}&tag=x&limit=7`)
		);
		expect(f.bucket).toEqual(['attention']);
		expect(f.sort).toBe('status');
		expect(f.site).toBe(UUID);
		expect(f.tag).toBeNull();
		expect(f.limit).toBe(100);
	});

	it('reads freshness, collector, and admin state', () => {
		const f = readDeviceFilters(
			new URLSearchParams(`freshness=stale&freshness=never&collector=${UUID}&admin_state=disabled`)
		);
		expect(f.freshness).toEqual(['stale', 'never']);
		expect(f.collector).toBe(UUID);
		expect(f.admin_state).toBe('disabled');
	});

	it('writes back only what differs from the defaults', () => {
		const f = readDeviceFilters(new URLSearchParams('bucket=active&sort=name'));
		expect(toQuery(f, 'status')).toBe('bucket=active&sort=name');
		expect(toQuery(readDeviceFilters(new URLSearchParams('')), 'status')).toBe('');
	});

	it('reads interface filters with WAN and interest', () => {
		const f = readInterfaceFilters(new URLSearchParams('wan=true&interest=primary&sort=traffic'));
		expect(f).toMatchObject({ wan: true, interest: 'primary', sort: 'traffic' });
		expect(readInterfaceFilters(new URLSearchParams('wan=maybe')).wan).toBeUndefined();
	});
});

describe('12.6, E.28: graph ranges', () => {
	it('defaults to 24h and keeps a preset or a custom range', () => {
		expect(readRange(new URLSearchParams(''))).toEqual({ preset: '24h' });
		expect(rangeParams(readRange(new URLSearchParams('range=400d')))).toEqual({ range: '400d' });
		const custom = readRange(
			new URLSearchParams('start=2026-10-01T00:00:00Z&end=2026-10-02T00:00:00Z')
		);
		expect(rangeParams(custom)).toEqual({
			start: '2026-10-01T00:00:00Z',
			end: '2026-10-02T00:00:00Z'
		});
	});

	it('refuses custom ranges outside the E.28 bounds', () => {
		const now = Date.UTC(2026, 9, 20);
		expect(checkCustom(now - 30_000, now, now)).toBe('A range is at least 1 minute.');
		expect(checkCustom(now - 401 * 86_400_000, now, now)).toBe('A range is at most 400 days.');
		expect(checkCustom(now, now + 3_600_000, now)).toBe(
			'A range may end at most 5 minutes from now.'
		);
		expect(checkCustom(now, now - 60_000, now)).toBe('The end must be after the start.');
		expect(checkCustom(now - 3_600_000, now, now)).toBeNull();
	});

	it('converts wall time in the display zone, across daylight saving', () => {
		expect(wallToEpoch('2026-07-01T12:00', 'America/Chicago')).toBe(Date.UTC(2026, 6, 1, 17));
		expect(wallToEpoch('2026-12-01T12:00', 'America/Chicago')).toBe(Date.UTC(2026, 11, 1, 18));
		expect(epochToWall(Date.UTC(2026, 6, 1, 17), 'America/Chicago')).toBe('2026-07-01T12:00');
		expect(Number.isNaN(wallToEpoch('nonsense', 'UTC'))).toBe(true);
	});
});

describe('TIME-003: times in the display zone', () => {
	it('formats in the named zone and falls back to UTC for an unknown one', () => {
		expect(formatInstant('2026-10-20T11:20:00Z', 'America/Chicago')).toBe('2026-10-20 06:20:00');
		expect(formatInstant('2026-10-20T11:20:00Z', 'Mars/Olympus')).toBe('2026-10-20 11:20:00');
		expect(knownZone('Mars/Olympus')).toBe(false);
		expect(zoneLabel('Mars/Olympus')).toContain('UTC (the browser does not know Mars/Olympus)');
		expect(zoneLabel('UTC', 'Europe/Berlin')).toBe('Times in UTC (site zone: Europe/Berlin)');
		expect(formatInstant(null, 'UTC')).toBe('—');
	});

	it('words ages', () => {
		expect([age(30), age(600), age(7200), age(259200), age(null)]).toEqual([
			'30 s',
			'10 min',
			'2 h',
			'3 d',
			'—'
		]);
	});
});

describe('12.6: SI network units; missing is a dash', () => {
	it('formats rates, percentages, and counts', () => {
		expect(bitRate(950)).toBe('950 bit/s');
		expect(bitRate(1_250_000)).toBe('1.25 Mbit/s');
		expect(bitRate(10e9)).toBe('10.0 Gbit/s');
		expect(bitRate(null)).toBe('—');
		expect(percent(42.123)).toBe('42.1%');
		expect(count(12345)).toBe('12,345');
		expect(count(undefined)).toBe('—');
	});
});

describe('links and sign-in return paths', () => {
	it('builds query strings without empty values', () => {
		expect(query({ a: '1', b: null, c: ['x', 'y'], d: false, e: '' })).toBe('?a=1&c=x&c=y');
	});

	it('returns only to same-origin paths after sign-in', () => {
		expect(safeNext('/devices?bucket=attention')).toBe('/devices?bucket=attention');
		for (const bad of [
			'//evil.example',
			'https://evil.example',
			'/\\evil',
			null,
			'/login?next=/'
		]) {
			expect(safeNext(bad)).toBe('/');
		}
	});
});

describe('ADR-035 #6, E.31: retry policy', () => {
	it('retries a backend outage at most twice, with growing delays', () => {
		const outage = new ApiFailure('unavailable', 503, {});
		expect(shouldRetry(0, outage)).toBe(true);
		expect(shouldRetry(MAX_RETRIES, outage)).toBe(false);
		expect(shouldRetry(0, new ApiFailure('invalid', 422, {}))).toBe(false);
		expect([0, 1, 2, 5].map(retryDelay)).toEqual([1000, 2000, 4000, 4000]);
	});
});
