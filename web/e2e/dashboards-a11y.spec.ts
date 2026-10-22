/**
 * Accessibility of custom dashboards: axe on the list, view, editor, kiosk page, and a rotation,
 * in both themes and at 480 px, where tables must scroll (WEB-T08, UI-011, UI-004).
 *
 * @packageDocumentation
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { newDashboard } from './dashboards-support.ts';
import { newToken, setUp, shown } from './kiosk-support.ts';
import { settled, signIn } from './support.ts';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

for (const scheme of ['light', 'dark'] as const) {
	test(`WEB-T08: axe finds no WCAG A/AA violation on dashboard pages (${scheme} theme)`, async ({
		page,
		request,
		browser
	}) => {
		await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
		const made = await newDashboard(request);
		const found: string[] = [];
		const scan = async (name: string, target = page) => {
			const result = await new AxeBuilder({ page: target }).withTags(TAGS).analyze();
			found.push(...result.violations.map((v) => `${name} ${v.id}: ${v.nodes[0]?.target}`));
		};
		await signIn(page);
		for (const path of ['/dashboards', `/dashboards/${made.id}`, '/dashboards/new',
			`/dashboards/${made.id}/edit`]) {
			await page.goto(path);
			await settled(page);
			await scan(path);
		} // prettier-ignore
		const display = await browser.newContext(); // a separate browser: the kiosk cookie only
		const kiosk = await display.newPage();
		await kiosk.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
		await setUp(kiosk, (await newToken(request)).token);
		for (const path of [`/kiosk/dashboard/${made.id}`,
			`/kiosk/rotate?pages=overview,dashboard:${made.id}&every=60`]) {
			await kiosk.goto(path);
			await shown(kiosk);
			await scan(path, kiosk);
		} // prettier-ignore
		// At 480 px the tables scroll; their scroll areas must take keyboard focus.
		for (const [target, path] of [[page, `/dashboards/${made.id}`],
			[kiosk, `/kiosk/dashboard/${made.id}`]] as const) {
			await target.setViewportSize({ width: 480, height: 900 });
			await target.goto(path);
			await (target === kiosk ? shown(kiosk) : settled(page));
			await scan(`${path} (narrow)`, target);
		} // prettier-ignore
		await display.close();
		expect(found).toEqual([]);
	});
}
