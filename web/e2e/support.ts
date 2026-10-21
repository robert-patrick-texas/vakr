/**
 * Shared helpers of the end-to-end tests: seeded UUIDs, sign-in, and watchers for CSP
 * violations and requests that leave the origin (WEB-T01 to WEB-T11).
 *
 * The harness (`tests/e2e/serve.py`) writes the UUIDs of the seeded fleet to
 * `test-results/.fixtures/<scenario>.json`. Sign-in goes through the API with
 * the page's own cookie jar, so every test starts signed in without typing
 * (the sign-in page itself is covered by `auth.spec.ts`).
 *
 * @packageDocumentation
 */
import { readFileSync } from 'node:fs';
import { expect, type Page } from '@playwright/test';

/** The seeded fleet's names and UUIDs. */
export interface Fixtures {
	scenario: string;
	anchor: string;
	devices: Record<string, string>;
	interfaces: Record<string, string>;
	collectors: Record<string, string>;
	sites: Record<string, string>;
	tags: Record<string, string>;
	groups: Record<string, string>;
	reviews: string[];
}

export const ADMIN = 'e2e-admin';
export const OPERATOR = 'e2e-operator';
export const DEGRADED = `http://127.0.0.1:${Number(process.env['VAKR_E2E_PORT'] ?? 18920) + 1}`;

/**
 * Read a scenario's fixtures.
 *
 * @param scenario - `normal` or `no-telemetry`.
 * @returns The names and UUIDs.
 */
export function fixtures(scenario = 'normal'): Fixtures {
	return JSON.parse(readFileSync(`test-results/.fixtures/${scenario}.json`, 'utf8')) as Fixtures;
}

/**
 * The password the config generated for an account.
 *
 * @param user - `e2e-admin` or `e2e-operator`.
 * @returns The password (never printed).
 */
export function passwordOf(user: string): string {
	const value =
		process.env[user === ADMIN ? 'VAKR_E2E_ADMIN_PASSWORD' : 'VAKR_E2E_OPERATOR_PASSWORD'];
	if (!value) throw new Error('the Playwright config sets the e2e passwords');
	return value;
}

/**
 * Sign in through the API with the page's cookies.
 *
 * @param page - The page.
 * @param user - Which account.
 * @param base - The server, when not the default one.
 */
export async function signIn(page: Page, user = ADMIN, base = ''): Promise<void> {
	const answer = await page.request.post(`${base}/api/v1/auth/login`, {
		data: { username: user, password: passwordOf(user) }
	});
	expect(answer.status()).toBe(200);
}

/**
 * Collect CSP violations the page reports (WEB-T10).
 *
 * @param page - The page, before its first navigation.
 * @returns A function that returns the violations so far.
 */
export async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
	await page.addInitScript(() => {
		const seen: string[] = [];
		(window as unknown as { __csp: string[] }).__csp = seen;
		document.addEventListener('securitypolicyviolation', (e) => {
			seen.push(`${e.violatedDirective} ${e.blockedURI}`);
		});
	});
	return () => page.evaluate(() => (window as unknown as { __csp: string[] }).__csp ?? []);
}

/**
 * Record every request whose origin differs from the page's (WEB-T10, UI-012).
 *
 * @param page - The page.
 * @param origin - The allowed origin.
 * @returns The list that fills as requests are made.
 */
export function watchOrigins(page: Page, origin: string): string[] {
	const foreign: string[] = [];
	page.on('request', (request) => {
		const url = request.url();
		if (!url.startsWith('data:') && new URL(url).origin !== origin) foreign.push(url);
	});
	return foreign;
}

/**
 * Wait until a page's data has loaded: no loading notice is left.
 *
 * @param page - The page.
 */
export async function settled(page: Page): Promise<void> {
	// The shell is empty until the app starts, so first wait for the page to render something.
	await expect(page.locator('main h1, main [data-state]').first()).toBeVisible();
	await expect(page.locator('[data-state="loading"]')).toHaveCount(0);
}
