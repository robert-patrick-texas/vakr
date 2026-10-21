/**
 * Pages tell stale, partial, empty, denied, collector trouble, telemetry trouble, and backend
 * trouble apart, and never call a device down for any of them (WEB-T07; UI-014, UI-015, UI-013).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { DEGRADED, OPERATOR, fixtures, settled, signIn } from './support.ts';

const ENVELOPE = {
	code: 'storage_unavailable',
	message: 'the metadata database is unavailable',
	request_id: 'e2e-request',
	fields: []
};

test('UI-015: an unhealthy collector makes the page partial, in words', async ({ page }) => {
	await signIn(page);
	await page.goto('/devices');
	await expect(page.locator('[data-state="partial"]')).toContainText(
		"Collector west: unavailable; its devices' states are less certain."
	);
});

test('UI-014: without telemetry, devices are Unknown, never Down, and graphs say why', async ({
	page
}) => {
	const fx = fixtures('no-telemetry');
	await signIn(page, 'e2e-admin', DEGRADED);
	await page.goto(`${DEGRADED}/devices`);
	await settled(page);
	await expect(page.locator('[data-state="partial"]')).toContainText('Telemetry unavailable');
	await expect(page.locator('table tbody [data-status="red"]')).toHaveCount(0);
	await expect(page.getByText('Down', { exact: true })).toHaveCount(0);
	await expect(page.getByText(/Unknown — telemetry unavailable/).first()).toBeVisible();
	await page.goto(`${DEGRADED}/interfaces/${fx.interfaces['core1/Gi0/0/1']}`);
	await expect(page.locator('[data-graph="traffic"] [data-state="partial"]')).toContainText(
		'Not available'
	);
});

test('UI-014: no answer from vakr-api is backend trouble, not device trouble', async ({ page }) => {
	await signIn(page);
	await page.route('**/api/v1/devices?**', (route) => route.abort('connectionrefused'));
	await page.goto('/devices');
	const alert = page.locator('[data-state="unavailable"]');
	await expect(alert).toContainText('Backend unavailable', { timeout: 20_000 });
	await expect(alert).toContainText('says nothing about the devices');
});

test('UI-014: an unavailable database is named, with the request ID', async ({ page }) => {
	await signIn(page);
	await page.route('**/api/v1/overview?**', (route) =>
		route.fulfill({ status: 503, json: ENVELOPE })
	);
	await page.goto('/');
	const alert = page.locator('[data-state="unavailable"]');
	await expect(alert).toContainText('the metadata database is unavailable', { timeout: 20_000 });
	await expect(alert).toContainText('request e2e-request');
});

test('UI-015: a failed refresh keeps the rows and says they are stale', async ({ page }) => {
	await page.clock.install();
	await signIn(page);
	await page.goto('/devices');
	await settled(page);
	await page.route('**/api/v1/devices?**', (route) =>
		route.fulfill({ status: 503, json: ENVELOPE })
	);
	await page.clock.fastForward('00:31');
	// The refresh and its two retries (1 s, then 2 s) run on the fake clock too.
	const stale = page.locator('[data-state="stale"]');
	await expect
		.poll(async () => {
			await page.clock.fastForward('00:02');
			return stale.count();
		})
		.toBe(1);
	await expect(stale).toContainText('The last refresh failed');
	await expect(page.locator('table tbody tr')).toHaveCount(10);
});

test('UI-013, 12.8: a refresh keeps keyboard focus and announces nothing new', async ({ page }) => {
	await page.clock.install();
	await signIn(page);
	await page.goto('/devices');
	await settled(page);
	const button = page.getByRole('columnheader', { name: /Name/ }).getByRole('button');
	await button.focus();
	const refreshed = page.waitForResponse('**/api/v1/devices?**');
	await page.clock.fastForward('00:31');
	await refreshed;
	await expect(button).toBeFocused();
	await expect(page.getByTestId('announcer')).toHaveText('');
});

test('UI-014: empty and permission-denied states', async ({ page }) => {
	await signIn(page, OPERATOR);
	await page.goto('/devices?search=no-such-device-anywhere');
	await expect(page.locator('[data-state="empty"]')).toHaveText('No device matches these filters.');
	await page.goto('/admin');
	await expect(page.locator('[data-state="denied"]')).toContainText('Permission denied');
	await expect(page.getByRole('link', { name: 'Admin' })).toHaveCount(0);
	await page.route('**/api/v1/collectors?**', (route) =>
		route.fulfill({ status: 403, json: { ...ENVELOPE, code: 'forbidden', message: 'no' } })
	);
	await page.goto('/collectors');
	await expect(page.locator('[data-state="denied"]').first()).toContainText('Permission denied');
});
