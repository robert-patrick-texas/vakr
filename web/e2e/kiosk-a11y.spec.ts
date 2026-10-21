/**
 * Accessibility of the kiosk display: axe on every kiosk page, the rotation, and the
 * full-screen states, in both themes with reduced motion (WEB-T08, WEB-T09; UI-011, UI-013).
 *
 * @packageDocumentation
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { newToken, setUp, shown } from './kiosk-support.ts';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const BOARDS = ['/kiosk/overview', '/kiosk/high-priority', '/kiosk/wan', '/kiosk/rankings'];

for (const scheme of ['light', 'dark'] as const) {
	test(`WEB-T08: axe finds no WCAG A/AA violation on kiosk pages (${scheme} theme)`, async ({
		page,
		request
	}) => {
		await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
		const { token } = await newToken(request);
		await page.goto('/kiosk/wan');
		await expect(page.locator('[data-state="kiosk-signed-out"]')).toBeVisible();
		const checks: string[] = ['/kiosk/wan (signed out)'];
		const found: string[] = [];
		const scan = async (name: string) => {
			const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
			found.push(...result.violations.map((v) => `${name} ${v.id}: ${v.nodes[0]?.target}`));
		};
		await scan(checks[0] ?? '');
		await page.goto('/kiosk/setup');
		await scan('/kiosk/setup');
		await setUp(page, token);
		await scan('/kiosk');
		for (const path of [...BOARDS, '/kiosk/rotate?pages=overview,wan,rankings&every=60']) {
			await page.goto(path);
			await shown(page);
			await scan(path);
		}
		// A screen narrower than the tables makes them scroll; with their links shown as text,
		// the scroll area itself must be reachable by keyboard (axe scrollable-region-focusable).
		await page.setViewportSize({ width: 480, height: 900 });
		for (const path of BOARDS) {
			await page.goto(path);
			await shown(page);
			await scan(`${path} (narrow)`);
			if (path === '/kiosk/high-priority') {
				const wide = page.locator('.table-wrap').first();
				expect(await wide.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true);
			}
		}
		await page.goto('/kiosk/rotate?pages=overview,weather');
		await expect(page.locator('[data-state="kiosk-invalid"]')).toBeVisible();
		await scan('/kiosk/rotate (invalid)');
		expect(found).toEqual([]);
	});
}

test('WEB-T08: the rotation pause button is reachable by keyboard and says its state', async ({
	page,
	request
}) => {
	await setUp(page, (await newToken(request)).token);
	await page.goto('/kiosk/rotate?pages=wan,rankings&every=60');
	await shown(page);
	const pause = page.getByRole('button', { name: 'Pause rotation' });
	for (let i = 0; i < 10 && !(await pause.evaluate((b) => b === document.activeElement)); i++) {
		await page.keyboard.press('Tab');
	}
	await expect(pause).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('button', { name: 'Resume rotation' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(page.getByTestId('announcer')).toHaveText('Rotation paused');
});
