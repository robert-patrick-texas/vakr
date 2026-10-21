/**
 * Global navigation, search, dashboards, and collector health (12.1, 12.2, UI-001, UI-008, UI-017).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { OPERATOR, settled, signIn } from './support.ts';

const NAV = [
	'Overview',
	'Devices',
	'Interfaces',
	'Sites',
	'Dashboards',
	'Collectors/Health',
	'Search'
];

test('12.1: the navigation has every item, Admin for admins only, and the release', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/');
	const nav = page.getByRole('navigation', { name: 'Main' });
	await expect(nav.getByRole('link')).toHaveText([...NAV, 'Admin']);
	await expect(nav.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
	await expect(page.getByText(/^vakr \d+\.\d+\.\d+/)).toBeVisible();
	await page.context().clearCookies();
	await signIn(page, OPERATOR);
	await page.goto('/devices');
	await expect(nav.getByRole('link')).toHaveText(NAV);
});

test('UI-017: an interface history is two interactions from the search box', async ({ page }) => {
	await signIn(page);
	await page.goto('/devices');
	await page.getByLabel('Search devices, interfaces, sites, circuits').fill('ACME internet');
	await page.keyboard.press('Enter');
	await page.locator('ul.hits li', { hasText: 'Interface' }).getByRole('link').first().click();
	await expect(page).toHaveURL(/\/interfaces\/[0-9a-f-]{36}/);
	await expect(page.locator('[data-graph="traffic"]')).toBeVisible();
});

test('UI-008, 12.2: the overview has every panel the API supports', async ({ page }) => {
	await signIn(page);
	await page.goto('/');
	await settled(page);
	for (const heading of [
		'Device state',
		'Recent events',
		'Quick filters',
		'Collectors, spool, and databases',
		'High-priority interfaces',
		'WAN circuits',
		'Top utilization and errors'
	]) {
		await expect(page.getByRole('heading', { name: heading })).toBeVisible();
	}
	await expect(page.getByText('Top utilization, p95 over 15m (east)')).toBeVisible();
	await page
		.getByRole('group', { name: 'Ranking window' })
		.getByRole('button', { name: '1h' })
		.click();
	await expect(page).toHaveURL(/window=1h/);
	await expect(page.getByText('Top utilization, p95 over 1h (east)')).toBeVisible();
});

test('UI-008: rankings, high priority, and WAN circuits', async ({ page }) => {
	await signIn(page);
	await page.goto('/rankings?window=1h&rows=25');
	await settled(page);
	await expect(page.getByText('Top errors over 1h (east)')).toBeVisible();
	await expect(
		page.locator('table:has(caption:text("Top errors")) tbody tr').first()
	).toContainText('Gi1/0/5');
	await page.goto('/high-priority');
	await settled(page);
	await expect(page.locator('table tbody tr')).toHaveCount(2);
	await page.goto('/wan');
	await settled(page);
	await expect(page.locator('table tbody th')).toHaveText(['ACME-1001', 'GLX-2002', 'GLX-77']);
});

test('UI-001: collector health names the healthy and the silent collector', async ({ page }) => {
	await signIn(page);
	await page.goto('/collectors');
	await settled(page);
	await expect(page.locator('tr[data-collector="east"] [data-status]')).toHaveAttribute(
		'data-status',
		'green'
	);
	await expect(page.locator('tr[data-collector="west"] [data-status]')).toHaveAttribute(
		'data-status',
		'red'
	);
	await expect(page.getByText(/This page is about vakr itself/)).toBeVisible();
});
