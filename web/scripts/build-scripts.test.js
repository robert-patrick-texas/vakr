/**
 * The build's CSP manifest, the announcer workaround, and the licence checks
 * (ADR-035 #13, #17; E.31; WEB-T10; SEC-017; UI-012).
 *
 * @packageDocumentation
 */
import { describe, expect, it } from 'vitest';
import { initialFiles, inspectHtml, scriptHash } from './csp-manifest.js';
import { stripAnnouncerStyle } from './vite-csp-announcer.ts';
import { packageOf } from './vite-bundled-packages.ts';
import { ALLOWED, BUNDLED, licenseAllowed, licenseProblems } from './check-licenses.js';

describe('csp-manifest', () => {
	it('hashes the exact inline script body as CSP does', () => {
		// sha256 of the empty string, base64.
		expect(scriptHash('')).toBe('sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=');
	});

	it('finds inline scripts but not external ones, and refuses inline styles and handlers', () => {
		const found = inspectHtml(
			'<script src="/a.js"></script><script>boot()</script><div style="x"></div><a onclick="y">'
		);
		expect(found.scripts).toEqual(['boot()']);
		expect(found.problems).toEqual(['a style attribute', 'an inline event handler']);
		expect(inspectHtml('<style>p{}</style>').problems).toEqual(['a <style> element']);
	});

	it('lists the files the page loads before the app runs', () => {
		const html =
			'<link href="/_app/immutable/a.js" rel="modulepreload"><link rel="stylesheet" href="/_app/immutable/b.css">' +
			'<link rel="icon" href="/favicon.svg"><script>import("/_app/immutable/c.js")</script>';
		expect(initialFiles(html)).toEqual([
			'_app/immutable/a.js',
			'_app/immutable/b.css',
			'_app/immutable/c.js'
		]);
	});
});

describe('vite-csp-announcer', () => {
	it('removes only the announcer style attribute', () => {
		const code = '<div id="svelte-announcer" aria-live="assertive" style="position: absolute">';
		expect(stripAnnouncerStyle(code)).toBe('<div id="svelte-announcer" aria-live="assertive">');
	});

	it('fails the build when SvelteKit changes the markup', () => {
		expect(() => stripAnnouncerStyle('<div id="other">')).toThrow(/review the CSP workaround/);
	});
});

describe('bundled packages and licences', () => {
	it('names the package of a module, nested copies included', () => {
		expect(packageOf('/w/node_modules/uplot/dist/uPlot.esm.js')).toBe('uplot');
		expect(packageOf('/w/node_modules/@tanstack/table-core/build/x.js?v=1')).toBe(
			'@tanstack/table-core'
		);
		expect(packageOf('/w/node_modules/svelte/node_modules/magic-string/x.js')).toBe('magic-string');
		expect(packageOf('/w/src/lib/links.ts')).toBeNull();
	});

	it('evaluates SPDX expressions', () => {
		expect(licenseAllowed('MIT', BUNDLED)).toBe(true);
		expect(licenseAllowed('(MIT OR CC0-1.0)', BUNDLED)).toBe(true);
		expect(licenseAllowed('MIT AND GPL-3.0-only', ALLOWED)).toBe(false);
		expect(licenseAllowed('MPL-2.0', ALLOWED)).toBe(true);
		expect(licenseAllowed('MPL-2.0', BUNDLED)).toBe(false);
		expect(licenseAllowed(undefined, ALLOWED)).toBe(false);
	});

	it('refuses a copyleft package, a bundled MPL package, and a bundled package missing from copyright', () => {
		const lock = {
			packages: {
				'': {},
				'node_modules/a': { version: '1', license: 'MIT' },
				'node_modules/b': { version: '1', license: 'GPL-3.0-only', dev: true },
				'node_modules/c': { version: '1', license: 'MPL-2.0' }
			}
		};
		expect(licenseProblems(lock, ['a', 'c'], 'names a only')).toEqual([
			'b@1: licence GPL-3.0-only is not allowed',
			'c: licence MPL-2.0 may not be bundled',
			'c: bundled but not named in packaging/copyright'
		]);
	});
});
