/**
 * Vite and SvelteKit configuration of the vakr web UI (ADR-035 decisions 1, 4, 9, 13).
 *
 * The app is a static single-page app: `adapter-static` writes one fallback
 * page (`index.html`) plus content-hashed files under `/_app/immutable/`,
 * which `vakr-api` serves same-origin outside `/api/`. Asset URLs are
 * absolute (`paths.relative: false`) because the fallback page answers deep
 * links such as `/devices/<uuid>`. `version.name` comes from
 * `SOURCE_DATE_EPOCH` (the commit time the package build sets), so two
 * builds of one commit are byte-identical (PKG-004); the release number is
 * never written here (VER-001).
 *
 * The dev server proxies `/api/` to a local `vakr api serve` (default
 * `http://127.0.0.1:8080`, or `VAKR_API_URL`), so cookies and CSRF stay
 * same-origin in development too.
 *
 * @packageDocumentation
 */
import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-static';
import { defineConfig } from 'vitest/config';
import { cspAnnouncer } from './scripts/vite-csp-announcer.ts';
import { bundledPackages } from './scripts/vite-bundled-packages.ts';

const apiTarget = process.env['VAKR_API_URL'] ?? 'http://127.0.0.1:8080';

export default defineConfig({
	plugins: [
		cspAnnouncer(),
		bundledPackages(),
		sveltekit({
			adapter: adapter({
				pages: 'build',
				assets: 'build',
				fallback: 'index.html',
				precompress: true,
				strict: true
			}),
			paths: { relative: false },
			version: { name: process.env['SOURCE_DATE_EPOCH'] ?? 'dev', pollInterval: 0 },
			output: { bundleStrategy: 'split' },
			compilerOptions: { runes: true }
		})
	],
	build: {
		// Charts are a separate chunk on purpose; the budget (Appendix E.31) is
		// checked against the initial chunks, not this warning.
		chunkSizeWarningLimit: 600,
		sourcemap: false
	},
	server: {
		host: '127.0.0.1',
		proxy: { '/api/': { target: apiTarget, changeOrigin: false } }
	},
	test: {
		include: ['src/**/*.test.ts', 'scripts/**/*.test.js'],
		environment: 'node'
	}
});
