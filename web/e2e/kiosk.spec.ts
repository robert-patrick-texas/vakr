/**
 * Kiosk displays end to end: setup, chrome-less dashboards, refresh without flashing, stale and
 * signed-out states, rotation, and bounded polling (UI-013, UI-014, UI-015, DASH-T06, WEB-T07,
 * WEB-T10).
 *
 * Tokens are created per test through the admin API with a separate request
 * context, so the display's browser holds only its kiosk cookie (a person's
 * session would win over it). Refreshes and rotation run on Playwright's fake
 * clock, so the real E.31 intervals and dwell times are used: nothing is
 * shortened for the tests.
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { newToken, setUp, shown, unique } from './kiosk-support.ts';
import { ADMIN, passwordOf, watchCsp, watchOrigins } from './support.ts';

test('UI-013, WEB-T10: a pasted token sets the display up; no navigation, no links, no other origin', async ({
	page,
	request,
	baseURL
}) => {
	const csp = await watchCsp(page);
	const foreign = watchOrigins(page, new URL(baseURL ?? '').origin);
	const { token } = await newToken(request);
	await setUp(page, token);
	expect(page.url()).not.toContain(token);
	const cookie = (await page.context().cookies()).find((c) => c.name === 'vakr_kiosk');
	expect(cookie).toMatchObject({ httpOnly: true, sameSite: 'Strict', path: '/' });
	await expect(page.getByRole('link', { name: 'WAN circuits' })).toBeVisible();
	for (const board of ['overview', 'high-priority', 'wan', 'rankings']) {
		await page.goto(`/kiosk/${board}`);
		await shown(page);
		await expect(page.getByRole('navigation')).toHaveCount(0);
		await expect(page.locator('main table a')).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Sign out' })).toHaveCount(0);
		await expect(page.getByTestId('kiosk-freshness')).toContainText('refreshes every');
	}
	expect(await csp()).toEqual([]);
	expect(foreign).toEqual([]);
});

test('UI-013: a refresh updates the table in place: same nodes, no loading notice', async ({
	page,
	request
}) => {
	await page.clock.install();
	await setUp(page, (await newToken(request)).token);
	await page.goto('/kiosk/wan');
	await shown(page);
	const table = await page.locator('main table').first().elementHandle();
	expect(table).not.toBeNull();
	await table?.evaluate((n) => n.setAttribute('data-marked', 'before-refresh'));
	const refreshed = page.waitForResponse('**/api/v1/wan-circuits**');
	await page.clock.fastForward('00:31');
	await refreshed;
	expect(await table?.evaluate((n) => n.isConnected && n.getAttribute('data-marked'))).toBe(
		'before-refresh'
	);
	await expect(page.locator('[data-state="loading"]')).toHaveCount(0);
});

test('UI-015, WEB-T07: when vakr-api stops answering, the rows stay and say they are stale', async ({
	page,
	request
}) => {
	await page.clock.install();
	await setUp(page, (await newToken(request)).token);
	await page.goto('/kiosk/high-priority');
	await shown(page);
	const rows = await page.locator('main table tbody tr').count();
	await page.route('**/api/v1/high-priority**', (route) => route.abort('connectionrefused'));
	await page.clock.fastForward('00:31');
	const stale = page.locator('[data-state="stale"]');
	await expect
		.poll(async () => {
			await page.clock.fastForward('00:02');
			return stale.count();
		})
		.toBe(1);
	await expect(stale).toContainText('The last refresh failed');
	await expect(page.locator('main table tbody tr')).toHaveCount(rows);
});

test('UI-014: a revoked token takes the whole screen at the next refresh, with what to do', async ({
	page,
	request
}) => {
	await page.clock.install();
	const { token, id, csrf } = await newToken(request);
	await setUp(page, token);
	await page.goto('/kiosk/overview');
	await shown(page);
	const gone = await request.delete(`/api/v1/kiosk-tokens/${id}`, { headers: csrf });
	expect(gone.status()).toBe(204);
	await page.clock.fastForward('00:31');
	const notice = page.locator('[data-state="kiosk-signed-out"]');
	await expect(notice).toContainText('This display is not signed in');
	await expect(notice).toContainText('sudo vakr kiosk create');
	await expect(page.locator('main table')).toHaveCount(0);
	await page.goto('/kiosk/wan');
	await expect(page.locator('[data-state="kiosk-signed-out"]')).toBeVisible();
	await expect(page).toHaveURL(/\/kiosk\/wan$/); // never the sign-in page
});

test('UI-013: an unknown dashboard or an option out of bounds is a full-screen error', async ({
	page,
	request
}) => {
	await setUp(page, (await newToken(request)).token);
	const cases: Array<[string, string]> = [
		['/kiosk/weather', 'no kiosk dashboard named'],
		['/kiosk/wan?refresh=5', 'from 15 to 3600'],
		['/kiosk/rotate?pages=overview,weather', 'no kiosk dashboard named "weather"'],
		['/kiosk/rotate?pages=overview&every=60', '2 to 8 dashboards'],
		['/kiosk/rotate?pages=overview,wan&every=5', 'from 15 to 3600']
	];
	for (const [path, says] of cases) {
		await page.goto(path);
		await expect(page.locator('[data-state="kiosk-invalid"]'), path).toContainText(says);
	}
});

test('UI-013, DASH-T06: rotation switches at the dwell time, and only the visible page polls', async ({
	page,
	request
}) => {
	await page.clock.install();
	await setUp(page, (await newToken(request)).token);
	const reads: string[] = [];
	page.on('request', (r) => {
		const path = new URL(r.url()).pathname;
		if (path.startsWith('/api/v1/') && path !== '/api/v1/auth/session') reads.push(path);
	});
	await page.goto('/kiosk/rotate?pages=overview,wan&every=60');
	await shown(page);
	await expect(page.locator('h1')).toHaveText('Overview');
	await expect(page.getByTestId('kiosk-position')).toContainText('1 of 2 · next: WAN circuits');
	await page.clock.fastForward('00:40');
	expect(reads.filter((p) => p === '/api/v1/overview').length).toBe(2); // first read and one refresh
	expect(reads).not.toContain('/api/v1/wan-circuits'); // the hidden page makes no requests
	await page.clock.fastForward('00:21');
	await expect(page.locator('h1')).toHaveText('WAN circuits');
	await expect(page.getByTestId('kiosk-position')).toContainText('2 of 2 · next: Overview');
	await expect(page.locator('[data-state="loading"]')).toHaveCount(0); // read ahead: no flash
	expect(reads.filter((p) => p === '/api/v1/wan-circuits').length).toBe(1);
	const overviewReads = reads.filter((p) => p === '/api/v1/overview').length;
	await page.clock.fastForward('00:50');
	expect(reads.filter((p) => p === '/api/v1/overview').length).toBe(overviewReads);
});

test('UI-013: rotation pauses while the tab is hidden and by the pause button', async ({
	page,
	request
}) => {
	await page.clock.install();
	await setUp(page, (await newToken(request)).token);
	await page.goto('/kiosk/rotate?pages=wan,rankings&every=15');
	await shown(page);
	await page.evaluate(() =>
		Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
	);
	await page.clock.fastForward('01:00');
	await expect(page.locator('h1')).toHaveText('WAN circuits');
	await page.evaluate(() =>
		Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
	);
	const pause = page.getByRole('button', { name: 'Pause rotation' });
	await pause.click();
	await page.clock.fastForward('00:30');
	await expect(page.locator('h1')).toHaveText('WAN circuits');
	await expect(page.getByTestId('kiosk-position')).toContainText('(paused)');
	await page.getByRole('button', { name: 'Resume rotation' }).click();
	await page.clock.fastForward('00:16');
	await expect(page.locator('h1')).toHaveText('Top utilization and errors');
});

test('E.12: an admin creates a token on the Admin page, sees it once, and revokes it', async ({
	page
}) => {
	const response = await page.request.post('/api/v1/auth/login', {
		data: { username: ADMIN, password: passwordOf(ADMIN) }
	});
	expect(response.status()).toBe(200);
	const label = unique('lobby');
	await page.goto('/admin');
	await page.getByLabel('Display label').fill(label);
	await page.getByRole('button', { name: 'Create token' }).click();
	const token = page.getByTestId('kiosk-new-token');
	await expect(token).toContainText('vkk_');
	await page.getByRole('button', { name: 'Done, hide it' }).click();
	await expect(page.getByTestId('kiosk-new-token')).toHaveCount(0);
	const row = page.getByRole('row', { name: new RegExp(label) });
	await expect(row).toContainText('live');
	await expect(page.locator('main')).not.toContainText('vkk_');
	await row.getByRole('button', { name: 'Revoke' }).click();
	await row.getByRole('button', { name: `Yes, revoke ${label}` }).click();
	await expect(page.getByTestId('kiosk-outcome')).toContainText(`Revoked kiosk token ${label}`);
	await expect(page.getByRole('row', { name: new RegExp(label) })).toHaveCount(0);
});
