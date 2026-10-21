/**
 * A Vite plugin that records which npm packages end up in the browser build
 * (UI-012, SEC-017; ADR-035 decision 17).
 *
 * Only the client build ships. In `generateBundle`, every module of every
 * client chunk is mapped to the package it came from, and the sorted list
 * is written to `.svelte-kit/vakr-bundled.json`. `csp-manifest.js` copies it
 * into `build/vakr-web.json`, and `check-licenses.js` holds those packages
 * to the stricter licence allowlist and checks that `packaging/copyright`
 * names each one.
 *
 * @packageDocumentation
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import type { Plugin } from 'vite';

export const BUNDLED_FILE = '.svelte-kit/vakr-bundled.json';

/**
 * Name the package a module id belongs to: the last `node_modules` segment wins (nested copies).
 *
 * @param id - A module id (a file path, possibly with a query).
 * @returns The package name, or `null` for vakr's own modules.
 */
export function packageOf(id: string): string | null {
	const path = id.split('?')[0]?.replaceAll('\\', '/') ?? '';
	const at = path.lastIndexOf('/node_modules/');
	if (at < 0) return null;
	const rest = path.slice(at + '/node_modules/'.length).split('/');
	const name = rest[0]?.startsWith('@') ? `${rest[0]}/${rest[1] ?? ''}` : rest[0];
	return name || null;
}

/**
 * Return the plugin.
 *
 * @returns A build-only plugin.
 */
export function bundledPackages(): Plugin {
	return {
		name: 'vakr-bundled-packages',
		apply: 'build',
		generateBundle(_options, bundle) {
			if (this.environment.name !== 'client') return;
			const found = new Set<string>();
			for (const output of Object.values(bundle)) {
				if (output.type !== 'chunk') continue;
				for (const id of output.moduleIds) {
					const name = packageOf(id);
					if (name) found.add(name);
				}
			}
			mkdirSync('.svelte-kit', { recursive: true });
			writeFileSync(BUNDLED_FILE, JSON.stringify([...found].sort(), null, 2) + '\n');
		}
	};
}
