/**
 * Local assets only, the CSP and security headers, caching, and the fallback (WEB-T10, UI-012,
 * SEC-013, 15.4; ADR-035 #3, #4, #12, #13).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { fixtures, settled, signIn, watchCsp, watchOrigins } from './support.ts';

/**
 * Every page of the app, with seeded subjects.
 *
 * @returns Paths.
 */
function pages(): string[] {
	const fx = fixtures();
	return [
		'/',
		'/devices',
		`/devices/${fx.devices['core1']}?range=6h`,
		`/devices/${fx.devices['core1']}?view=graphs`,
		'/interfaces',
		`/interfaces/${fx.interfaces['core1/Gi0/0/1']}?range=6h`,
		'/sites',
		'/dashboards',
		'/rankings',
		'/high-priority',
		'/wan',
		'/collectors',
		'/identity-reviews',
		'/admin',
		'/search?q=core'
	];
}

test('WEB-T10, UI-012: no request leaves the origin and the CSP is never violated', async ({
	page,
	baseURL
}) => {
	const violations = await watchCsp(page);
	const foreign = watchOrigins(page, new URL(baseURL ?? '').origin);
	await signIn(page);
	for (const path of pages()) {
		await page.goto(path);
		await settled(page);
		await page.waitForLoadState('networkidle');
		expect(await violations(), path).toEqual([]);
	}
	expect(foreign).toEqual([]);
});

test('WEB-T10: pages carry the hashed CSP and the 15.4 headers, and revalidate', async ({
	page
}) => {
	const answer = await page.request.get('/devices/anything');
	expect(answer.status()).toBe(200);
	const h = answer.headers();
	expect(h['content-type']).toBe('text/html; charset=utf-8');
	expect(h['cache-control']).toBe('no-cache');
	expect(h['content-security-policy']).toMatch(/script-src 'self' 'sha256-[A-Za-z0-9+/]{43}='/);
	expect(h['content-security-policy']).not.toMatch(/unsafe-inline|unsafe-eval/);
	for (const d of [
		"default-src 'self'",
		"style-src 'self'",
		"connect-src 'self'",
		"frame-ancestors 'none'",
		"base-uri 'none'",
		"object-src 'none'"
	]) {
		expect(h['content-security-policy']).toContain(d);
	}
	expect(h['x-frame-options']).toBe('DENY');
	expect(h['x-content-type-options']).toBe('nosniff');
	expect(h['referrer-policy']).toBe('no-referrer');
	expect(h['cross-origin-opener-policy']).toBe('same-origin');
	expect(h['access-control-allow-origin']).toBeUndefined();
});

test('ADR-035 #4: hashed assets are immutable; API paths keep the JSON envelope', async ({
	page
}) => {
	const html = await (await page.request.get('/')).text();
	const asset = /\/_app\/immutable\/[^"]+\.js/.exec(html)?.[0];
	expect(asset).toBeTruthy();
	const answer = await page.request.get(asset ?? '');
	expect(answer.headers()['cache-control']).toBe('public, max-age=31536000, immutable');
	expect(answer.headers()['content-type']).toContain('text/javascript');
	const api = await page.request.get('/api/v1/no-such-endpoint');
	expect(api.status()).toBe(404);
	expect((await api.json()).code).toBe('not_found');
	expect((await page.request.get('/_app/immutable/chunks/missing.js')).status()).toBe(404);
});
