/**
 * Automated accessibility checks, keyboard reach, and state without colour (WEB-T08, WEB-T09;
 * UI-011, 12.8).
 *
 * axe-core checks WCAG 2.0 to 2.2 A and AA rules on every main page, in the
 * light and the dark theme and with reduced motion. The keyboard test walks
 * the skip link, the navigation, and a sortable header. WEB-T09 checks that
 * every status has an icon and words, and that no two meanings share an
 * icon, so the state reads in grayscale and for colour-blind users.
 *
 * @packageDocumentation
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { fixtures, settled, signIn } from './support.ts';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/**
 * The pages axe checks.
 *
 * @returns Paths.
 */
function pages(): string[] {
	const fx = fixtures();
	return [
		'/',
		'/devices',
		`/devices/${fx.devices['core1']}?range=6h`,
		'/interfaces',
		`/interfaces/${fx.interfaces['core1/Gi0/0/1']}?range=6h`,
		'/rankings',
		'/wan',
		'/collectors',
		'/identity-reviews',
		'/search?q=core'
	];
}

for (const scheme of ['light', 'dark'] as const) {
	test(`WEB-T08: axe finds no WCAG A/AA violation (${scheme} theme)`, async ({ page }) => {
		await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
		await page.goto('/login');
		const login = await new AxeBuilder({ page }).withTags(TAGS).analyze();
		expect(login.violations.map((v) => v.id)).toEqual([]);
		await signIn(page);
		for (const path of pages()) {
			await page.goto(path);
			await settled(page);
			const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
			const found = result.violations.map((v) => `${v.id}: ${v.nodes[0]?.target.join(' ')}`);
			expect(found, path).toEqual([]);
		}
	});
}

test('WEB-T08: keyboard reaches the skip link, navigation, search, and sorting', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/');
	await settled(page);
	await page.keyboard.press('Tab');
	await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('link', { name: 'vakr', exact: true })).toBeFocused();
	await page.keyboard.press('Tab');
	await page.keyboard.press('Tab');
	await expect(page.getByRole('link', { name: 'Devices', exact: true })).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/devices$/);
	await settled(page);
	await page.getByLabel('Search devices, interfaces, sites, circuits').focus();
	await page.keyboard.type('core1');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/search\?q=core1/);
	await page.getByRole('link', { name: 'core1', exact: true }).first().focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { level: 1, name: 'core1' })).toBeVisible();
});

test('WEB-T09: every status shows an icon and words; no icon stands for two colours', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/devices?include_retired=true');
	await settled(page);
	const colourOf = new Map<string, string>();
	for (const badge of await page.locator('table tbody [data-status]').all()) {
		const icon = (await badge.locator('.icon').textContent())?.trim() ?? '';
		const label = (await badge.locator('.label').textContent())?.trim() ?? '';
		const colour = (await badge.getAttribute('data-status')) ?? '';
		expect(icon).not.toBe('');
		expect(label).not.toBe('');
		await expect(badge.locator('.icon')).toHaveAttribute('aria-hidden', 'true');
		expect(colourOf.get(icon) ?? colour, `icon ${icon}`).toBe(colour);
		colourOf.set(icon, colour);
	}
	expect(colourOf.get('✖')).toBe('red');
	expect(colourOf.get('✔')).toBe('green');
	expect(new Set(colourOf.values())).toEqual(new Set(['red', 'amber', 'green', 'gray']));
});
