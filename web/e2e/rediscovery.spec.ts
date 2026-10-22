/**
 * Manual rediscovery in the web UI: admins only, immediate answer, followed to the end
 * (DISC-003, DISC-010, API-011, DISC-T03, SEC-006).
 *
 * The e2e harness runs no collector, so the progress test answers the
 * request and the operation reads itself (queued, running, succeeded); the
 * refusal test is the real API: the seeded devices have no SNMP credential,
 * so none can be rediscovered.
 *
 * @packageDocumentation
 */
import { expect, test, type Page } from '@playwright/test';
import { fixtures, OPERATOR, settled, signIn } from './support.ts';

const OP = '0190a3c4-0000-7000-8000-00000000f00d';
const RESULT = {
	interfaces: 24,
	new: 1,
	renumbered: 0,
	missing: 2,
	reviews_opened: 1,
	capability_changes: 0,
	partial: false,
	duration_ms: 1800
};

/**
 * An operation in a given state, as the API answers it.
 *
 * @param device - Device UUID.
 * @param state - The state.
 * @returns The answer body.
 */
function op(device: string, state: string): Record<string, unknown> {
	const done = state === 'succeeded';
	return {
		id: OP,
		kind: 'rediscovery',
		device_id: device,
		device_name: 'core1',
		state,
		requested_by: 'e2e-admin',
		requested_at: '2026-10-21T12:00:00Z',
		started_at: state === 'queued' ? null : '2026-10-21T12:00:02Z',
		finished_at: done ? '2026-10-21T12:00:04Z' : null,
		collector_id: state === 'queued' ? null : fixtures().collectors['east'],
		result: done ? RESULT : null,
		error: null
	};
}

/**
 * Answer the device's operation list with nothing, and the operation with each state in turn.
 *
 * @param page - The page.
 * @param device - Device UUID.
 * @returns Called before each read, the states served so far.
 */
async function answerOperations(page: Page, device: string): Promise<string[]> {
	const served: string[] = [];
	const states = ['queued', 'running', 'succeeded'];
	await page.route('**/api/v1/operations?*', (route) =>
		route.fulfill({ json: { rows: [], page: { limit: 1, total: 0, next_cursor: null } } })
	);
	await page.route(`**/api/v1/operations/${OP}`, (route) => {
		const state = states[Math.min(served.length, states.length - 1)] ?? 'succeeded';
		served.push(state);
		return route.fulfill({ json: op(device, state) });
	});
	await page.route('**/rediscovery', (route) =>
		route.fulfill({ status: 202, json: op(device, 'queued') })
	);
	return served;
}

test('SEC-006: an operator sees no Rediscover button', async ({ page }) => {
	await signIn(page, OPERATOR);
	await page.goto(`/devices/${fixtures().devices['core1']}`);
	await settled(page);
	await expect(page.getByRole('heading', { level: 1, name: 'core1' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Rediscover now' })).toHaveCount(0);
	await expect(page.getByTestId('rediscovery')).toHaveCount(0);
});

test('DISC-010: an admin requests, the page follows the operation to its result', async ({
	page
}) => {
	await signIn(page);
	const device = fixtures().devices['core1'] ?? '';
	const served = await answerOperations(page, device);
	await page.goto(`/devices/${device}`);
	await settled(page);
	const button = page.getByRole('button', { name: 'Rediscover now' });
	const posted = page.waitForRequest('**/rediscovery');
	await button.click();
	expect((await posted).headers()['x-csrf-token']).toBeTruthy();
	const status = page.getByTestId('rediscovery-status');
	await expect(status).toHaveAttribute('data-state', /queued|running/);
	await expect(button).toBeDisabled();
	await expect(status).toHaveAttribute('data-state', 'succeeded', { timeout: 15_000 });
	await expect(status).toContainText(
		'Succeeded: 24 interfaces, 1 new, 0 renumbered, 2 missing, 1 identity reviews opened in 1.8 s'
	);
	await expect(button).toBeEnabled();
	expect(served).toContain('running');
	const reads = served.length;
	await page.waitForTimeout(2500);
	expect(served.length).toBe(reads); // a finished operation is not polled again
});

test('DISC-003: a device no collector would discover is refused with the reason', async ({
	page
}) => {
	await signIn(page);
	await page.goto(`/devices/${fixtures().devices['core1']}`);
	await settled(page);
	await page.getByRole('button', { name: 'Rediscover now' }).click();
	await expect(page.getByRole('alert')).toContainText(
		'Not possible: core1 has no SNMP credential, so there is nothing to discover.'
	);
	await expect(page.getByTestId('rediscovery-status')).toHaveCount(0);
});

test('API-011: the Admin page lists recent operations', async ({ page }) => {
	await signIn(page);
	const device = fixtures().devices['core1'] ?? '';
	await page.route('**/api/v1/operations?*', (route) =>
		route.fulfill({
			json: { rows: [op(device, 'succeeded')], page: { limit: 25, total: 1, next_cursor: null } }
		})
	);
	await page.goto('/admin');
	await settled(page);
	const row = page.getByTestId('operations').locator('tbody tr');
	await expect(row).toHaveCount(1);
	await expect(row).toContainText('core1');
	await expect(row).toContainText('Succeeded: 24 interfaces');
	await expect(row.getByRole('link', { name: 'core1' })).toHaveAttribute(
		'href',
		`/devices/${device}`
	);
});
