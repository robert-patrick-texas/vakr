/**
 * Copy the licence and notice files of every package bundled into the web UI to one directory
 * (UI-012, SEC-017; packaging/copyright points at it).
 *
 * The minified build keeps no licence comments, so the package build copies
 * each bundled package's `LICENSE`, `COPYING`, and `NOTICE` files (Apache-2.0
 * requires its NOTICE to travel with the code) into
 * `/opt/vakr/share/doc/web-licenses/<package>/`.
 *
 * Usage: `node scripts/collect-licenses.js DIR` after `npm run build`.
 *
 * @packageDocumentation
 */
import { copyFileSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const LEGAL = /^(licen[cs]e|copying|notice)(\.(md|txt))?$/i;

/**
 * Copy one package's legal files.
 *
 * @param name - The package name, such as `@tanstack/table-core`.
 * @param target - The destination root.
 * @returns How many files were copied.
 */
export function collect(name, target) {
	const source = join('node_modules', name);
	const files = readdirSync(source).filter((f) => LEGAL.test(f));
	const dest = join(target, name.replace('/', '__'));
	mkdirSync(dest, { recursive: true });
	for (const file of files) copyFileSync(join(source, file), join(dest, file));
	return files.length;
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const target = process.argv[2];
	if (!target) {
		console.error('usage: node scripts/collect-licenses.js DIR');
		process.exit(2);
	}
	const { bundled } = JSON.parse(readFileSync('build/vakr-web.json', 'utf8'));
	const missing = bundled.filter((name) => collect(name, target) === 0);
	if (missing.length > 0) {
		console.error(`collect-licenses: no licence file in ${missing.join(', ')}`);
		process.exit(1);
	}
	console.log(`collect-licenses: ${bundled.length} packages' licence files in ${target}`);
}
