/**
 * Custom dashboards: an admin builds one, operators view it, kiosks show and rotate it
 * (UI-004, UI-013, DB-003, DASH-T04, DASH-T06, SEC-006).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { newDashboard } from './dashboards-support.ts';
import { newToken, setUp, shown, unique } from './kiosk-support.ts';
import { OPERATOR, fixtures, settled, signIn } from './support.ts';

test('UI-004: an admin creates a dashboard, edits it, and pins an interface from its page', async ({
	page
}) => {
	await signIn(page);
	const name = unique('built');
	await page.goto('/dashboards/new');
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByRole('button', { name: 'Add', exact: true }).click(); // pinned interfaces
	await page.getByLabel('Find an interface').fill('ACME internet');
	await page.locator('#main').getByRole('button', { name: 'Search' }).click();
	await page
		.getByRole('list', { name: 'Interfaces found' })
		.getByRole('button', { name: 'Pin' })
		.first()
		.click();
	await page.getByLabel('Add a widget').selectOption('interface_selection');
	await page.getByRole('button', { name: 'Add', exact: true }).click();
	await page.locator('#w1-site_id').selectOption({ label: 'Austin' });
	await expect(page.getByRole('list', { name: 'Before saving' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Save' }).click();
	await expect(page).toHaveURL(/\/dashboards\/[0-9a-f-]{36}$/);
	await expect(page.locator('h1')).toHaveText(name);
	await expect(page.locator('[data-widget="pinned_interfaces"] tbody tr')).toHaveCount(1);
	await expect(page.locator('[data-widget="interface_selection"]')).toContainText('site Austin');

	await page.getByRole('link', { name: 'Edit' }).click();
	await page.getByLabel('Name', { exact: true }).fill(`${name} v2`);
	await page.getByRole('button', { name: 'Save' }).click();
	await expect(page.locator('h1')).toHaveText(`${name} v2`);
	const board = page.url();

	await page.goto(`/interfaces/${fixtures().interfaces['core1/Gi0/0/2']}`);
	await page.getByLabel('Dashboard', { exact: true }).selectOption({ label: `${name} v2` });
	await page.getByRole('button', { name: 'Pin', exact: true }).click();
	await expect(page.getByTestId('pin-outcome')).toHaveText(`Pinned to ${name} v2.`);
	await page.goto(board);
	await expect(page.locator('[data-widget="pinned_interfaces"] tbody tr')).toHaveCount(2);
});

test('SEC-006: an operator views a dashboard but gets no edit or pin controls', async ({
	page,
	request
}) => {
	const made = await newDashboard(request);
	await signIn(page, OPERATOR);
	await page.goto('/dashboards');
	await expect(page.getByRole('link', { name: made.name })).toBeVisible();
	await expect(page.getByRole('link', { name: 'New dashboard' })).toHaveCount(0);
	await page.goto(`/dashboards/${made.id}`);
	await settled(page);
	await expect(page.locator('h1')).toHaveText(made.name);
	await expect(page.getByRole('link', { name: 'Edit' })).toHaveCount(0);
	await expect(page.locator('[data-widget="device_selection"]')).toContainText('group backbone');
	await page.goto(`/interfaces/${fixtures().interfaces['core1/Gi0/0/1']}`);
	await settled(page);
	await expect(page.getByRole('heading', { name: 'Pin to dashboard' })).toHaveCount(0);
});

test('DB-003: saving over someone else’s change is refused and offers a reload', async ({
	page,
	request
}) => {
	const made = await newDashboard(request);
	await signIn(page);
	await page.goto(`/dashboards/${made.id}/edit`);
	await expect(page.getByLabel('Name', { exact: true })).toHaveValue(made.name);
	const other = await request.patch(`/api/v1/dashboards/${made.id}`, {
		data: { version: made.version, description: 'changed elsewhere' },
		headers: made.csrf
	});
	expect(other.status()).toBe(200);
	await page.getByLabel('Description').fill('my change');
	await page.getByRole('button', { name: 'Save' }).click();
	await expect(page.getByTestId('editor-outcome')).toContainText('Someone changed this dashboard');
	await page.getByRole('button', { name: 'Reload the dashboard' }).click();
	await expect(page.getByLabel('Description')).toHaveValue('changed elsewhere');
});

test('DASH-T06: one dashboard refresh is one view request, at the dashboard’s interval', async ({
	page,
	request
}) => {
	const made = await newDashboard(request, undefined, 15);
	await page.clock.install();
	await signIn(page);
	const views: string[] = [];
	page.on('request', (r) => {
		if (new URL(r.url()).pathname.endsWith(`/dashboards/${made.id}/view`)) views.push(r.url());
	});
	await page.goto(`/dashboards/${made.id}`);
	await expect(page.getByTestId('dashboard-freshness')).toContainText('refreshes every 15 s');
	for (const total of [2, 3]) {
		// TanStack restarts its interval when an answer arrives, so wait for each answer.
		const answered = page.waitForResponse((r) => r.url().endsWith(`/dashboards/${made.id}/view`));
		await page.clock.runFor('00:16');
		await answered;
		expect(views.length).toBe(total); // exactly one more read per refresh
	}
});

test('UI-004, UI-013: a custom dashboard on a kiosk display, in a rotation, and once deleted', async ({
	page,
	request
}) => {
	const made = await newDashboard(request);
	await page.clock.install();
	// newToken signs the admin in again, so its CSRF header is the one that counts now.
	const { token, csrf } = await newToken(request);
	await setUp(page, token);
	await expect(page.getByRole('link', { name: made.name })).toBeVisible(); // the chooser lists it
	await page.goto(`/kiosk/dashboard/${made.id}`);
	await shown(page);
	await expect(page.locator('h1')).toHaveText(made.name);
	await expect(page.getByRole('navigation')).toHaveCount(0);
	await expect(page.locator('main table a')).toHaveCount(0);
	await page.goto(`/kiosk/rotate?pages=wan,dashboard:${made.id}&every=15`);
	await shown(page);
	await expect(page.getByTestId('kiosk-position')).toContainText(`next: ${made.name}`);
	await page.clock.fastForward('00:16');
	await expect(page.locator('h1')).toHaveText(made.name);
	await expect(page.locator('[data-state="loading"]')).toHaveCount(0); // read ahead: no flash
	const gone = await request.delete(`/api/v1/dashboards/${made.id}`, { headers: csrf });
	expect(gone.status()).toBe(204);
	await page.goto(`/kiosk/dashboard/${made.id}`);
	await expect(page.locator('[data-state="kiosk-invalid"]')).toContainText('no longer exists');
	await page.goto('/kiosk/rotate?pages=wan,dashboard:nope');
	await expect(page.locator('[data-state="kiosk-invalid"]')).toContainText('dashboard:nope');
});
