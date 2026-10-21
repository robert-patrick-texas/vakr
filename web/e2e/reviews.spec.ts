/**
 * Identity reviews: operators read, admins decide, with CSRF and refusals handled (IFID-007,
 * SEC-005, SEC-011). The decision changes the seeded data, so it runs last in this file.
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { OPERATOR, settled, signIn } from './support.ts';

test.describe.configure({ mode: 'serial' });

test('SEC-005: an operator reads reviews without the decision controls', async ({ page }) => {
	await signIn(page, OPERATOR);
	await page.goto('/identity-reviews');
	await settled(page);
	await expect(page.locator('article.review')).toHaveCount(1);
	await expect(page.getByText('You can read these reviews; an admin decides them.')).toBeVisible();
	await expect(page.getByRole('button', { name: /^It is/ })).toHaveCount(0);
});

test('UI-014: a refused decision is shown as permission denied, and nothing changes', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/identity-reviews');
	await settled(page);
	await page.route('**/identity-resolution', (route) =>
		route.fulfill({
			status: 403,
			json: { code: 'forbidden', message: 'admin only', request_id: 'r', fields: [] }
		})
	);
	await page.getByRole('button', { name: 'It is Te0/0/5' }).click();
	await page.getByRole('button', { name: 'Confirm' }).click();
	await expect(page.getByTestId('decision-outcome')).toHaveText(
		'Permission denied: only an admin may decide reviews.'
	);
	await expect(page.locator('article.review')).toHaveCount(1);
});

test('IFID-007: an admin maps the review after confirming; the token goes with it', async ({
	page
}) => {
	await signIn(page);
	await page.goto('/identity-reviews');
	await settled(page);
	await page.getByRole('button', { name: 'It is Te0/0/5' }).click();
	await expect(page.getByRole('alert')).toContainText('Confirm: ifIndex 5 is Te0/0/5?');
	const posted = page.waitForRequest('**/identity-resolution');
	await page.getByRole('button', { name: 'Confirm' }).click();
	const request = await posted;
	expect(request.headers()['x-csrf-token']).toBeTruthy();
	expect(request.postDataJSON()).toMatchObject({ decision: 'map' });
	await expect(page.locator('[data-state="empty"]')).toHaveText('No identity review is open.');
});
