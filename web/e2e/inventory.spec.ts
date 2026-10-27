/**
 * Manual inventory entry in the web UI: add, change, and retire a device; annotate an interface;
 * bulk-tag the interfaces not yet classified (27.4, META-007, META-008, META-009, META-011,
 * META-013, DB-003, MPOS-003).
 *
 * Every write goes to the real API of the `normal` harness. The seeded
 * `edge-b` and its `Gi0/0/1` are managed by `inventory.toml`, so their
 * pages are read-only. A peer node's refusal is the API's envelope, served
 * by the test, since the harness runs a primary. Tests that change shared
 * interfaces put them back, so the specs after this one see the seed.
 *
 * @packageDocumentation
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { adminCsrf, unique } from './kiosk-support.ts';
import { fixtures, OPERATOR, settled, signIn } from './support.ts';

/**
 * Add a vakr tag to interfaces, or remove it, through the API (to put the seed back).
 *
 * @param request - A request context.
 * @param ids - Interface UUIDs.
 * @param tag - The tag.
 * @param add - Add (true) or remove (false).
 */
async function retag(request: APIRequestContext, ids: string[], tag: string, add: boolean) {
	const csrf = await adminCsrf(request);
	const body = { interfaces: ids, add: add ? [tag] : [], remove: add ? [] : [tag] };
	const answer = await request.post('/api/v1/interfaces/tags', { data: body, headers: csrf });
	expect(answer.status()).toBe(200);
}

/**
 * Fill the device form's name and address.
 *
 * @param page - The page.
 * @param name - Device name.
 * @param address - Poll address.
 */
async function nameAndAddress(page: Page, name: string, address: string): Promise<void> {
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByLabel('Poll address').fill(address);
}

test('27.4, META-009, DB-003: an admin adds, changes, and retires a device', async ({
	page,
	request
}) => {
	const name = unique('e2e-added');
	await signIn(page);
	await page.goto('/devices');
	await page.getByRole('link', { name: 'Add a device' }).click();
	await nameAndAddress(page, name, '192.0.2.250');
	await page.getByLabel('Site', { exact: true }).selectOption('Austin');
	await page.getByLabel('Tags', { exact: true }).fill('lab, e2e');
	await page.getByRole('button', { name: 'Add device' }).click();
	const saved = page.getByTestId('device-saved');
	await expect(saved).toContainText(`Added ${name}`);
	await expect(saved).toContainText('Warning:'); // no SNMP credential (E.37)
	await saved.getByRole('link', { name: 'Open the device page' }).click();
	await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
	await page.getByRole('link', { name: 'Edit device' }).click();
	await page.getByLabel('Comment', { exact: true }).fill('added by the e2e test');
	await page.getByRole('button', { name: 'Save changes' }).click();
	await expect(page.getByTestId('device-saved')).toContainText('comment changed');
	// Someone else saves: the form, still on the old version, must not overwrite them (DB-003).
	const id = new URL(page.url()).pathname.split('/')[2] ?? '';
	const csrf = await adminCsrf(request);
	const stored = await (await request.get(`/api/v1/devices/${id}/record`)).json();
	const other = { version: stored.version, comment: 'changed elsewhere' };
	expect((await request.patch(`/api/v1/devices/${id}`, { data: other, headers: csrf })).ok()).toBe(
		true
	);
	await page.getByLabel('Poll interval').fill('120s');
	await page.getByRole('button', { name: 'Save changes' }).click();
	await expect(page.getByRole('alert')).toContainText('Someone changed this device');
	await page.getByRole('button', { name: 'Reload the device' }).click();
	await expect(page.getByLabel('Comment', { exact: true })).toHaveValue('changed elsewhere');
	await page.getByRole('button', { name: 'Retire device' }).click();
	await page.getByRole('button', { name: `Yes, retire ${name}` }).click();
	await expect(page).toHaveURL(new RegExp(`/devices/${id}$`));
	await expect(page.locator('.device-header')).toContainText('retired');
});

test('META-008, CONF-008: problems show beside their fields; a file-managed device is read-only', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/devices/new');
	await nameAndAddress(page, 'core1', '169.254.169.254');
	await page.getByRole('button', { name: 'Add device' }).click();
	await expect(page.getByRole('alert')).toContainText('correct the fields');
	await expect(page.locator('#dev-name-note')).toContainText('already the name of device');
	await expect(page.locator('#dev-address-note')).toContainText('denied');
	await expect(page.getByLabel('Poll address')).toHaveAttribute('aria-invalid', 'true');
	await expect(page.getByRole('alert')).toBeFocused();
	await page.goto(`/devices/${fixtures().devices['edge-b']}/edit`);
	await settled(page);
	await expect(page.getByTestId('managed-by-file')).toContainText('inventory.toml');
	await expect(page.getByLabel('Poll address')).toBeDisabled();
	await expect(page.locator('#dev-address-note')).toContainText('Managed by inventory.toml');
	await expect(page.getByRole('button', { name: 'Save changes' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Retire device' })).toBeDisabled();
});

test('MPOS-003: on a peer the refusal names the primary', async ({ page }) => {
	await signIn(page);
	await page.route('**/api/v1/devices', (route) =>
		route.request().method() === 'POST'
			? route.fulfill({
					status: 409,
					json: {
						code: 'peer_read_only',
						message:
							'this node is a peer; its primary is https://vakr-east.example.net: change metadata there (MPOS-003)',
						request_id: 'e2e',
						fields: []
					}
				})
			: route.fallback()
	);
	await page.goto('/devices/new');
	await nameAndAddress(page, 'peer-test', '192.0.2.251');
	await page.getByRole('button', { name: 'Add device' }).click();
	await expect(page.getByRole('alert')).toContainText('https://vakr-east.example.net');
});

test('META-007, META-011: an admin annotates an interface; vakr tags stay apart', async ({
	page,
	request
}) => {
	const id = fixtures().interfaces['access10/Gi1/0/2'] ?? '';
	await signIn(page);
	await page.goto(`/interfaces/${id}`);
	await settled(page);
	const facts = page.getByTestId('classification');
	await expect(facts).toContainText('Not yet classified');
	await page.getByLabel('Provider').fill('E2E Carrier');
	await page.getByLabel('Contracted rate (bit/s)').fill('10 Mb');
	await expect(page.locator('#ann-contracted_bps-note')).toContainText('Whole bits per second');
	await page.getByLabel('Contracted rate (bit/s)').fill('10000000');
	await page.getByLabel('vakr tags').fill('e2e-uplink');
	await page.getByRole('button', { name: 'Save annotation' }).click();
	await expect(page.getByTestId('annotation-saved')).toContainText('provider: none -> E2E Carrier');
	await expect(facts).toContainText('e2e-uplink');
	await expect(facts).toContainText('Classified');
	await expect(facts.getByText('Yes', { exact: true })).toBeVisible();
	// Put the seed back for the specs that follow.
	const csrf = await adminCsrf(request);
	const now = await (await request.get(`/api/v1/interfaces/${id}/annotation`)).json();
	const back = { version: now.version, provider: null, contracted_bps: null, tags: [] };
	const reset = await request.patch(`/api/v1/interfaces/${id}/annotation`, {
		data: back,
		headers: csrf
	});
	expect(reset.status()).toBe(200);
	await page.goto(`/interfaces/${fixtures().interfaces['edge-b/Gi0/0/1']}`);
	await settled(page);
	await expect(page.getByTestId('managed-by-file')).toContainText('inventory.toml');
	await expect(page.getByLabel('Provider')).toBeDisabled();
});

test('META-013: bulk tagging works through the interfaces not yet classified', async ({
	page,
	request
}) => {
	const fx = fixtures();
	const ids = [fx.interfaces['access2/Gi1/0/1'] ?? '', fx.interfaces['access2/Gi1/0/2'] ?? ''];
	await signIn(page);
	await page.goto('/interfaces?unclassified=true&search=access2');
	await settled(page);
	await expect(page.getByLabel('Only not yet classified')).toBeChecked();
	await page.getByLabel('Select all 2 on this page').check();
	await expect(page.getByTestId('bulk-status')).toContainText('2 selected');
	await page.getByLabel('Tags to add').fill('e2e-bulk');
	await page.getByRole('button', { name: 'Apply to 2' }).click();
	await expect(page.getByTestId('bulk-status')).toContainText('Tagged 2 interface(s)');
	await expect(page.getByText('No interface matches these filters.')).toBeVisible();
	await page.goto('/interfaces?search=edge-b');
	await settled(page);
	await page.getByLabel('Select edge-b Gi0/0/0').check();
	await page.getByLabel('Select edge-b Gi0/0/1').check();
	await page.getByLabel('Tags to add').fill('e2e-bulk');
	await page.getByRole('button', { name: 'Apply to 2' }).click();
	const alert = page.getByRole('alert');
	await expect(alert).toContainText('inventory.toml');
	await expect(alert).toContainText('Managed by a file: edge-b Gi0/0/1');
	await retag(request, ids, 'e2e-bulk', false);
	await page.goto(`/devices/${fx.devices['access2']}`);
	await settled(page);
	await page.getByRole('button', { name: 'Show other interfaces' }).click();
	await page.getByLabel('Only not yet classified').check();
	await expect(page.getByRole('link', { name: 'Gi1/0/1' })).toBeVisible();
});

test('SEC-006: operators see no edit controls', async ({ page }) => {
	await signIn(page, OPERATOR);
	await page.goto('/devices');
	await settled(page);
	await expect(page.getByRole('link', { name: 'Add a device' })).toHaveCount(0);
	await page.goto(`/interfaces/${fixtures().interfaces['access10/Gi1/0/2']}`);
	await settled(page);
	await expect(page.getByTestId('classification')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Save annotation' })).toHaveCount(0);
	await page.goto('/interfaces');
	await settled(page);
	await expect(page.getByRole('heading', { name: 'Tag selected interfaces' })).toHaveCount(0);
	await page.goto('/devices/new');
	await expect(page.getByText('Only an admin can add devices.')).toBeVisible();
});
