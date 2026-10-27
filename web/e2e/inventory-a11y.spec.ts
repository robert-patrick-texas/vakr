/**
 * Accessibility of the manual inventory pages: axe on the device form (blank, with problems,
 * file-managed), the interface annotation form, and the bulk-tag list, in both themes; and a
 * keyboard-only add (WEB-T08, WEB-T09, UI-011, 27.4).
 *
 * @packageDocumentation
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { fixtures, settled, signIn } from './support.ts';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

for (const scheme of ['light', 'dark'] as const) {
	test(`WEB-T08: axe finds no WCAG A/AA violation on the inventory forms (${scheme} theme)`, async ({
		page
	}) => {
		await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
		const fx = fixtures();
		const found: string[] = [];
		const scan = async (name: string) => {
			const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
			found.push(...result.violations.map((v) => `${name} ${v.id}: ${v.nodes[0]?.target}`));
		};
		await signIn(page);
		for (const path of ['/devices/new', `/devices/${fx.devices['core1']}/edit`,
			`/devices/${fx.devices['edge-b']}/edit`, `/interfaces/${fx.interfaces['access10/Gi1/0/1']}`,
			`/interfaces/${fx.interfaces['edge-b/Gi0/0/1']}`, '/interfaces?unclassified=true']) {
			await page.goto(path);
			await settled(page);
			await scan(path);
		} // prettier-ignore
		// Problems beside their fields, and a selection with the bulk bar active.
		await page.goto('/devices/new');
		await page.getByLabel('Name', { exact: true }).fill('core1');
		await page.getByLabel('Poll address').fill('not an address!');
		await page.getByRole('button', { name: 'Add device' }).click();
		await expect(page.getByRole('alert')).toBeVisible();
		await scan('/devices/new (problems)');
		await page.goto('/interfaces?search=access2');
		await settled(page);
		await page.getByLabel('Select access2 Gi1/0/1').check();
		await scan('/interfaces (selected)');
		expect(found).toEqual([]);
	});
}

test('WEB-T08: the device form works from the keyboard alone', async ({ page }) => {
	await signIn(page);
	await page.goto('/devices/new');
	await settled(page);
	await page.getByLabel('Name', { exact: true }).focus();
	await page.keyboard.type('core1');
	await page.keyboard.press('Tab');
	await expect(page.getByLabel('Poll address')).toBeFocused();
	await page.keyboard.type('192.0.2.10');
	await page.keyboard.press('Enter'); // submits the form
	await expect(page.getByRole('alert')).toBeFocused(); // the problems are read out first
	await expect(page.locator('#dev-name-note')).toContainText('Problem:'); // words, not colour
});
