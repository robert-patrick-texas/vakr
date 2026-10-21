/**
 * Helpers of the kiosk end-to-end tests: tokens through the admin API, setup through the paste
 * form, and waiting for a dashboard's first answer (UI-013).
 *
 * Tokens are created with a separate request context signed in as the admin,
 * so the display's browser holds only its kiosk cookie (a person's session
 * in the same browser would win over it, E.30 #17). Tokens are generated per
 * test and never printed.
 *
 * @packageDocumentation
 */
import { expect, type APIRequestContext, type Page } from '@playwright/test';
import { ADMIN, passwordOf } from './support.ts';

/**
 * A label no other test uses.
 *
 * @param name - Readable part.
 * @returns The label.
 */
export function unique(name: string): string {
	return `${name}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

/**
 * Sign the admin in on a request context and return its CSRF header.
 *
 * @param request - A request context with its own cookies.
 * @returns The header.
 */
export async function adminCsrf(request: APIRequestContext): Promise<Record<string, string>> {
	const login = await request.post('/api/v1/auth/login', {
		data: { username: ADMIN, password: passwordOf(ADMIN) }
	});
	expect(login.status()).toBe(200);
	return { 'X-CSRF-Token': ((await login.json()) as { csrf_token: string }).csrf_token };
}

/**
 * Create a kiosk token as the admin.
 *
 * @param request - A request context (signed in here).
 * @param label - The display label.
 * @returns The token and its record ID.
 */
export async function newToken(
	request: APIRequestContext,
	label = unique('wall')
): Promise<{ token: string; id: string; csrf: Record<string, string> }> {
	const csrf = await adminCsrf(request);
	const made = await request.post('/api/v1/kiosk-tokens', { data: { label }, headers: csrf });
	expect(made.status()).toBe(201);
	const body = (await made.json()) as { token: string; kiosk: { id: string } };
	return { token: body.token, id: body.kiosk.id, csrf };
}

/**
 * Set the display up through the paste form.
 *
 * @param page - The display's page (no cookies yet).
 * @param token - Its kiosk token.
 */
export async function setUp(page: Page, token: string): Promise<void> {
	await page.goto('/kiosk/setup');
	await page.getByLabel('Kiosk token').fill(token);
	await page.getByRole('button', { name: 'Set up' }).click();
	await expect(page).toHaveURL(/\/kiosk$/);
}

/**
 * Wait until a kiosk dashboard shows its first answer.
 *
 * @param page - The page.
 */
export async function shown(page: Page): Promise<void> {
	await expect(page.getByTestId('kiosk-freshness')).toContainText('Data as of');
	await expect(page.locator('[data-state="loading"]')).toHaveCount(0);
}
