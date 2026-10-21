/**
 * The interface page: gaps and markers, resolution and zone, filters kept on the way back
 * (WEB-T03, WEB-T04, WEB-T06; UI-002, UI-010, TIME-003, STATE-005, RATE-004).
 *
 * @packageDocumentation
 */
import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { fixtures, settled, signIn } from './support.ts';

const zoned = new Intl.DateTimeFormat('en-CA', {
	timeZone: 'America/Chicago',
	year: 'numeric',
	month: '2-digit',
	day: '2-digit',
	hour: '2-digit',
	minute: '2-digit',
	second: '2-digit',
	hourCycle: 'h23'
});

/**
 * Format a UTC time in America/Chicago as the page does.
 *
 * @param iso - UTC ISO time.
 * @returns `YYYY-MM-DD HH:MM:SS`.
 */
function chicago(iso: string): string {
	const p = Object.fromEntries(zoned.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
	return `${p['year']}-${p['month']}-${p['day']} ${p['hour']}:${p['minute']}:${p['second']}`;
}

test.beforeEach(async ({ page }) => {
	await signIn(page);
});

test('WEB-T04: missing and invalid-baseline intervals are gaps and markers, never zero', async ({
	page
}) => {
	const id = fixtures().interfaces['core1/Gi0/0/1'];
	const api = await (
		await page.request.get(`/api/v1/interfaces/${id}/history/traffic?range=6h`)
	).json();
	const inbound = api.series.find((s: { metric: string }) => s.metric === 'in_bps');
	const nulls = inbound.points.filter((p: { value: number | null }) => p.value === null).length;
	expect(nulls).toBeGreaterThan(0);
	await page.goto(`/interfaces/${id}?range=6h`);
	await settled(page);
	const traffic = page.locator('[data-graph="traffic"]');
	const row = traffic.locator('table.summary tr', { hasText: 'Inbound (east)' });
	await expect(row.locator('td').last()).toHaveText(`${inbound.points.length} (${nulls})`);
	await traffic.getByText(/Quality markers/).click();
	for (const kind of ['gap', 'baseline', 'discontinuity']) {
		await expect(traffic.locator('.markers li strong', { hasText: kind }).first()).toBeVisible();
	}
	const gapAt = inbound.points.findIndex((p: { value: number | null }) => p.value === null);
	const slider = traffic.getByRole('slider');
	await slider.fill(String(gapAt));
	await expect(slider).toHaveAttribute('aria-valuetext', /Inbound \(east\) no data \(gap\)/);
});

test('WEB-T06: the range picks native or rollup data and the zone is the configured one', async ({
	page
}) => {
	const id = fixtures().interfaces['core1/Gi0/0/2'];
	await page.goto(`/interfaces/${id}?range=6h`);
	await settled(page);
	const traffic = page.locator('[data-graph="traffic"]');
	await expect(traffic.getByTestId('zone')).toHaveText('Times in America/Chicago');
	await expect(traffic.getByTestId('resolution')).toContainText('native samples');
	const api = await (
		await page.request.get(`/api/v1/interfaces/${id}/history/traffic?range=6h`)
	).json();
	const last = api.series[0].points
		.filter((p: { value: number | null }) => p.value !== null)
		.at(-1);
	const row = traffic.locator('table.summary tr', { hasText: api.series[0].label });
	await expect(row.locator('td').nth(1)).toHaveText(chicago(last.t));
	await page
		.getByRole('group', { name: 'Graph range' })
		.getByRole('button', { name: '90d' })
		.click();
	await expect(page).toHaveURL(/range=90d/);
	await expect(traffic.getByTestId('resolution')).toContainText('rollup', { timeout: 20_000 });
});

test('WEB-T03: a link from a filtered list keeps the filters on the way back', async ({ page }) => {
	await page.goto('/interfaces?provider=ACME');
	await settled(page);
	await page.getByRole('link', { name: 'Gi0/0/1', exact: true }).first().click();
	await expect(page).toHaveURL(/\/interfaces\/[0-9a-f-]+\?.*from=/);
	await page.getByRole('link', { name: '← Back to the list' }).click();
	await expect(page).toHaveURL(/\/interfaces\?provider=ACME$/);
});

test('ADR-035 #9: the ECharts chunk loads only with the state timeline', async ({ page }) => {
	const manifest = JSON.parse(readFileSync('build/vakr-web.json', 'utf8'));
	const chunk: string = manifest.lazy.echarts;
	const scripts: string[] = [];
	page.on('request', (r) => {
		if (r.resourceType() === 'script') scripts.push(new URL(r.url()).pathname);
	});
	await page.goto('/');
	await settled(page);
	await page.goto(`/devices/${fixtures().devices['core1']}`);
	await settled(page);
	expect(scripts).not.toContain(`/${chunk}`);
	await page.goto(`/interfaces/${fixtures().interfaces['core1/Gi0/0/1']}?range=6h`);
	await expect(page.locator('.timeline canvas')).toBeVisible();
	expect(scripts).toContain(`/${chunk}`);
	await expect(page.getByText(/^State segments \(/)).toBeVisible();
});
