/**
 * The device page: completeness, primary interfaces first, bounded graph cards, latency smoke
 * (WEB-T02, WEB-T03, WEB-T05, WEB-T11; UI-003, UI-006, UI-016, UI-017, ICMP-009).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { fixtures, settled, signIn } from './support.ts';

test.beforeEach(async ({ page }) => {
	await signIn(page);
});

test('WEB-T02: the device page shows system, reachability, SNMP, uptime, and freshness', async ({
	page
}) => {
	await page.goto(`/devices/${fixtures().devices['core1']}`);
	await settled(page);
	await expect(page.getByRole('heading', { level: 1, name: 'core1' })).toBeVisible();
	const facts = page.getByTestId('device-facts');
	for (const label of [
		'ICMP',
		'Probes',
		'Latency',
		'Jitter',
		'SNMP',
		'sysUpTime',
		'Poll interval',
		'Last poll',
		'Freshness',
		'Collector',
		'Comment'
	]) {
		await expect(facts.locator('dt', { hasText: new RegExp(`^${label}$`) })).toHaveCount(1);
	}
	await expect(page.getByText(/not proof of chassis or OS uptime/)).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Interfaces (30)' })).toBeVisible();
});

test('WEB-T03, UI-017: primary interfaces in full; others on demand, sticky and WAN first', async ({
	page
}) => {
	await page.goto(`/devices/${fixtures().devices['core1']}`);
	await settled(page);
	const tables = page.locator('table:has(caption:text("Primary interfaces"))');
	await expect(tables.locator('tbody tr td:nth-child(2) a')).toHaveText(['Gi0/0/3', 'Gi0/0/4']);
	const otherRequests: string[] = [];
	page.on('request', (r) => {
		if (r.url().includes('interest=secondary')) otherRequests.push(r.url());
	});
	expect(otherRequests).toEqual([]);
	await page.getByRole('button', { name: 'Show other interfaces' }).click();
	const other = page.locator(
		'table:has(caption:text("Other interfaces")) tbody tr td:nth-child(2) a'
	);
	await expect(other.first()).toHaveText('Te1/0/1');
	expect((await other.allTextContents()).slice(0, 4)).toEqual([
		'Te1/0/1',
		'Te1/0/2',
		'Gi0/0/1',
		'Gi0/0/2'
	]);
	expect(otherRequests.length).toBeGreaterThan(0);
	await expect(page.getByText(/Page 1: 25 of 28 interfaces/)).toBeVisible();
});

test('WEB-T11, UI-016: graph cards in priority order, bounded pages, lazy bounded graphs', async ({
	page
}) => {
	const graphs: string[] = [];
	const lists: string[] = [];
	page.on('request', (r) => {
		const url = new URL(r.url());
		if (url.pathname.endsWith('/history/traffic')) graphs.push(url.search);
		if (url.pathname.endsWith('/interfaces') && url.pathname.includes('/devices/'))
			lists.push(url.search);
	});
	await page.goto(`/devices/${fixtures().devices['core1']}?view=graphs`);
	const cards = page.locator('article.graph-card');
	await expect(cards).toHaveCount(12);
	expect((await cards.locator('h3 a').allTextContents()).slice(0, 6)).toEqual([
		'Te1/0/1',
		'Te1/0/2',
		'Gi0/0/1',
		'Gi0/0/2',
		'Gi0/0/3',
		'Gi0/0/4'
	]);
	expect(lists.some((q) => q.includes('limit=12'))).toBe(true);
	expect(lists.every((q) => /limit=(12|500)\b/.test(q) || q.includes('interest='))).toBe(true);
	// Below the fold, no card has asked for its graph yet.
	await expect(page.locator('[data-state="deferred"]')).toHaveCount(12);
	expect(graphs).toEqual([]);
	await cards.first().scrollIntoViewIfNeeded();
	await expect.poll(() => graphs.length).toBeGreaterThan(0);
	expect(graphs.length).toBeLessThanOrEqual(12);
	expect(graphs.every((q) => q.includes('max_points=120'))).toBe(true);
	await page.getByRole('button', { name: 'Next' }).click();
	await expect(cards.first().locator('h3 a')).not.toHaveText('Te1/0/1');
	await expect(cards).toHaveCount(12);
});

test('WEB-T05, ICMP-009: the smoke and loss match the source data', async ({ page }) => {
	const id = fixtures().devices['core1'];
	await page.goto(`/devices/${id}?range=6h`);
	await settled(page);
	const source = await (
		await page.request.get(`/api/v1/devices/${id}/history/reachability?range=6h`)
	).json();
	const east = source.positions.find((p: { position: string }) => p.position === 'east');
	const losses = east.loss.points.map((p: { value: number | null }) => p.value);
	const lossy = losses.filter((v: number | null) => v !== null && v > 0).length;
	const dark = losses.filter((v: number | null) => v === 100).length;
	expect([lossy, dark]).toEqual([3, 1]);
	const section = page.getByRole('region', { name: 'Latency from east' });
	await expect(section.getByText(`Polls with loss: ${lossy} of`)).toBeVisible();
	await expect(section.getByText(`with every probe lost: ${dark}.`)).toBeVisible();
	const probes = east.probes.flatMap((p: { rtts_us: (number | null)[] }) => p.rtts_us);
	const answered = probes.filter((r: number | null) => r !== null).length;
	await expect(section.getByText(`${answered} of ${probes.length} probes answered`)).toBeVisible();
	await expect(
		section.locator('table.summary tr', { hasText: 'Loss (east)' }).locator('td').nth(4)
	).toHaveText('100 %');
});
