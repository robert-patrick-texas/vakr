/**
 * Playwright end-to-end tests of the web UI against a real `vakr api serve` (WEB-T01 to WEB-T11).
 *
 * Two servers start before the tests, each `python -m tests.e2e.serve` from
 * the repository root on a freshly seeded SQLite install (`tests/e2e/`):
 * `normal` (the fleet) and `no-telemetry` (no telemetry database, for the
 * UI-014 states). Both serve the production build in `web/build`, same
 * origin, exactly as the package does.
 *
 * The two accounts' passwords are generated here, once per run, and passed
 * to the servers and the workers through the environment; they are never
 * written to a file or printed. Set `PW_WS_ENDPOINT` to use a browser
 * running elsewhere (for example the Playwright container on a host without
 * Chromium's system libraries); CI runs Chromium directly.
 *
 * @packageDocumentation
 */
import { randomBytes } from 'node:crypto';
import { defineConfig, devices } from '@playwright/test';

process.env['VAKR_E2E_ADMIN_PASSWORD'] ??= randomBytes(24).toString('base64url');
process.env['VAKR_E2E_OPERATOR_PASSWORD'] ??= randomBytes(24).toString('base64url');

export const NORMAL_PORT = Number(process.env['VAKR_E2E_PORT'] ?? 18920);
export const DEGRADED_PORT = NORMAL_PORT + 1;
const ws = process.env['PW_WS_ENDPOINT'];

/**
 * One harness server.
 *
 * @param port - Loopback port.
 * @param scenario - `normal` or `no-telemetry`.
 * @returns The webServer entry.
 */
function server(port: number, scenario: string) {
	return {
		command:
			`uv run python -m tests.e2e.serve --port ${port} --web-dir web/build ` +
			`--fixtures-out web/test-results/.fixtures/${scenario}.json --scenario ${scenario}`,
		cwd: '..',
		url: `http://127.0.0.1:${port}/api/v1/health/live`,
		reuseExistingServer: false,
		timeout: 120_000,
		gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 15_000 },
		// The server logs every request; VAKR_E2E_SERVER_LOG=1 shows it.
		stdout: 'ignore' as const,
		stderr: process.env['VAKR_E2E_SERVER_LOG'] ? ('pipe' as const) : ('ignore' as const)
	};
}

export default defineConfig({
	testDir: 'e2e',
	outputDir: 'test-results/artifacts',
	fullyParallel: false,
	workers: 1,
	retries: process.env['CI'] ? 1 : 0,
	timeout: 60_000,
	expect: { timeout: 10_000 },
	reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
	use: {
		baseURL: `http://127.0.0.1:${NORMAL_PORT}`,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure',
		...(ws ? { connectOptions: { wsEndpoint: ws } } : {})
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'], viewport: { width: 1400, height: 1000 } }
		}
	],
	webServer: [server(NORMAL_PORT, 'normal'), server(DEGRADED_PORT, 'no-telemetry')]
});
