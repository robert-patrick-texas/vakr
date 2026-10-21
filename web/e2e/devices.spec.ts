/**
 * The device list: default order, filters, browsing, and sorting (WEB-T01, UI-005, UI-009, 12.3).
 *
 * @packageDocumentation
 */
import { expect, test, type Page } from '@playwright/test';
import { fixtures, settled, signIn } from './support.ts';

/**
 * The device names of the table, top to bottom.
 *
 * @param page - The device list.
 * @returns Names.
 */
async function names(page: Page): Promise<string[]> {
	await settled(page);
	return page.locator('table tbody tr td:nth-child(2) a').allTextContents();
}

test.beforeEach(async ({ page }) => {
	await signIn(page);
});

test('WEB-T01: Attention first, then Active, then Offline/Retired', async ({ page }) => {
	await page.goto('/devices');
	expect(await names(page)).toEqual([
		'branch3',
		'branch9',
		'branch7',
		'access2',
		'access10',
		'core1',
		'dist1',
		'edge-a',
		'edge-b',
		'lab-sw'
	]);
	await expect(page.getByText('Attention: 3')).toBeVisible();
	const first = page.locator('table tbody tr').first();
	await expect(first.locator('[data-status="red"]')).toHaveText(/Down/);
});

test('WEB-T01: retired devices come last when included', async ({ page }) => {
	await page.goto('/devices?include_retired=true');
	const list = await names(page);
	expect(list.at(-1)).toBe('old-rtr');
	expect(list.indexOf('lab-sw')).toBeLessThan(list.indexOf('old-rtr'));
});

test('12.3: bucket and freshness filters live in the URL', async ({ page }) => {
	await page.goto('/devices');
	await settled(page);
	await page.getByRole('checkbox', { name: 'Attention' }).check();
	await expect(page).toHaveURL(/bucket=attention/);
	expect(await names(page)).toEqual(['branch3', 'branch9', 'branch7']);
	await page.goto('/devices?freshness=stale');
	expect(await names(page)).toEqual(['branch9']);
});

test('UI-009: browsing by site names the grouping and lists only its devices', async ({ page }) => {
	const fx = fixtures();
	await page.goto(`/devices?site=${fx.sites['Dallas DC1']}`);
	await expect(page.getByTestId('browsing')).toContainText('site Dallas DC1');
	const rows = page.locator('table tbody tr');
	await expect(rows.first()).toBeVisible();
	for (const site of await rows.locator('td:nth-child(4)').allTextContents()) {
		expect(site.trim()).toBe('Dallas DC1');
	}
});

test('12.8: a sortable header states its order and announces a change', async ({ page }) => {
	await page.goto('/devices');
	await settled(page);
	const state = page.getByRole('columnheader', { name: /State/ });
	const name = page.getByRole('columnheader', { name: /Name/ });
	await expect(state).toHaveAttribute('aria-sort', 'ascending');
	await expect(name).toHaveAttribute('aria-sort', 'none');
	await name.getByRole('button').focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/sort=name/);
	await expect(name).toHaveAttribute('aria-sort', 'ascending');
	await expect(page.getByTestId('announcer')).toHaveText('Sorted by Name, A to Z');
	expect((await names(page)).slice(0, 3)).toEqual(['access2', 'access10', 'branch3']);
});
