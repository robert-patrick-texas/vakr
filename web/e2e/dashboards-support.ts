/**
 * Helpers for the custom-dashboard e2e tests: create dashboards through the API as the admin.
 *
 * @packageDocumentation
 */
import { expect, type APIRequestContext } from '@playwright/test';
import { adminCsrf, unique } from './kiosk-support.ts';
import { fixtures } from './support.ts';

/** A created dashboard and the admin's CSRF header. */
export interface Made {
	id: string;
	name: string;
	version: number;
	csrf: Record<string, string>;
}

/**
 * The widgets most tests use: two pinned interfaces and the backbone devices.
 *
 * @returns Widget bodies.
 */
export function sampleWidgets(): unknown[] {
	const fx = fixtures();
	return [
		{
			kind: 'pinned_interfaces',
			title: 'Uplinks',
			interfaces: [fx.interfaces['core1/Gi0/0/1'], fx.interfaces['core1/Gi0/0/2']]
		},
		{
			kind: 'device_selection',
			title: 'Backbone devices',
			criteria: { group_id: fx.groups['backbone'] }
		}
	];
}

/**
 * Create a dashboard as the admin.
 *
 * @param request - Playwright's API context.
 * @param widgets - Widget bodies; `sampleWidgets()` by default.
 * @param refresh - Seconds between refreshes.
 * @returns The dashboard.
 */
export async function newDashboard(
	request: APIRequestContext,
	widgets: unknown[] = sampleWidgets(),
	refresh = 60
): Promise<Made> {
	const csrf = await adminCsrf(request);
	const name = unique('board');
	const made = await request.post('/api/v1/dashboards', {
		data: { name, refresh_s: refresh, widgets },
		headers: csrf
	});
	expect(made.status(), await made.text()).toBe(201);
	const body = (await made.json()) as { id: string; version: number };
	return { id: body.id, name, version: body.version, csrf };
}
