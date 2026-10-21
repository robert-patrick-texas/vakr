/**
 * Sign-in, sign-out, and an ended session (SEC-010, SEC-011, AUTH-002, UI-014).
 *
 * @packageDocumentation
 */
import { expect, test } from '@playwright/test';
import { ADMIN, passwordOf, signIn } from './support.ts';

test('SEC-010: sign-in returns to the page that was asked for', async ({ page }) => {
	await page.goto('/devices?bucket=attention');
	await expect(page).toHaveURL(/\/login\?next=%2Fdevices%3Fbucket%3Dattention$/);
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password').fill(passwordOf(ADMIN));
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/devices\?bucket=attention$/);
	await expect(page.getByRole('heading', { level: 1, name: 'Devices' })).toBeVisible();
});

test('AUTH-004: a refused sign-in says only that it failed', async ({ page }) => {
	await page.goto('/login');
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password').fill('not-the-password-of-this-run');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('alert')).toHaveText(
		'Sign-in failed: check the username and password.'
	);
	await expect(page.getByLabel('Password')).toHaveValue('');
});

test('SEC-011: sign-out sends the CSRF token and ends the session', async ({ page }) => {
	await signIn(page);
	await page.goto('/');
	const logout = page.waitForRequest('**/api/v1/auth/logout');
	await page.getByRole('button', { name: 'Sign out' }).click();
	const request = await logout;
	expect(request.headers()['x-csrf-token']).toBeTruthy();
	await expect(page).toHaveURL(/\/login/);
	await expect(page.getByText('You are signed out.')).toBeVisible();
	expect((await page.request.get('/api/v1/auth/session')).status()).toBe(401);
});

test('UI-014: a session that ends while a page is open leads to sign-in with the reason', async ({
	page,
	context
}) => {
	await signIn(page);
	await page.goto('/devices');
	await expect(page.locator('table tbody tr').first()).toBeVisible();
	await context.clearCookies();
	await page.getByRole('link', { name: 'Interfaces', exact: true }).click();
	await expect(page).toHaveURL(/\/login\?next=%2Finterfaces/);
	await expect(page.getByText(/Your session ended/)).toBeVisible();
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password').fill(passwordOf(ADMIN));
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/interfaces$/);
});
