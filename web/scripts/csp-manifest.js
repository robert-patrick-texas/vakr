/**
 * Write `build/vakr-web.json`: the CSP hashes of the inline bootstrap and the measured
 * initial bundle (ADR-035 decisions 13 and 21; Appendix E.31).
 *
 * SvelteKit's fallback page starts the app with one small inline `<script>`.
 * `vakr-api` allows it by its SHA-256 hash instead of `unsafe-inline`, and
 * reads the hash from this manifest at startup. The script also refuses a
 * build whose HTML carries inline styles or event-handler attributes, which
 * the CSP would block, and a build whose initial JavaScript or CSS exceeds
 * the E.31 budget (gzip).
 *
 * Usage: `node scripts/csp-manifest.js build` (run by `npm run build`).
 *
 * @packageDocumentation
 */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

/** Initial JavaScript budget, gzip bytes (Appendix E.31). */
export const JS_BUDGET = 120 * 1024;
/** Initial CSS budget, gzip bytes (Appendix E.31). */
export const CSS_BUDGET = 16 * 1024;

/**
 * Return the CSP source for an inline script body.
 *
 * @param body - The exact text between `<script>` and `</script>`.
 * @returns `sha256-<base64>`.
 */
export function scriptHash(body) {
	return 'sha256-' + createHash('sha256').update(body, 'utf8').digest('base64');
}

/**
 * Find inline scripts and CSP problems in one HTML document.
 *
 * @param html - The document.
 * @returns The inline script bodies and the problems found.
 */
export function inspectHtml(html) {
	const scripts = [];
	const problems = [];
	for (const match of html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi)) {
		const attrs = match[1] ?? '';
		if (!/\ssrc\s*=/i.test(attrs)) scripts.push(match[2] ?? '');
	}
	if (/<style[\s>]/i.test(html)) problems.push('a <style> element');
	if (/\sstyle\s*=/i.test(html)) problems.push('a style attribute');
	if (/<[a-z][^>]*\son[a-z]+\s*=/i.test(html)) problems.push('an inline event handler');
	return { scripts, problems };
}

/**
 * List the files `index.html` loads before the app runs.
 *
 * @param html - The fallback page.
 * @returns Paths relative to the build root.
 */
export function initialFiles(html) {
	const files = new Set();
	for (const match of html.matchAll(/<link[^>]+href="\/([^"]+)"[^>]*>/gi)) {
		const tag = match[0];
		if (/rel="(modulepreload|stylesheet)"/i.test(tag) && match[1]) files.add(match[1]);
	}
	for (const match of html.matchAll(/import\("\/([^"]+)"\)/g)) {
		if (match[1]) files.add(match[1]);
	}
	return [...files].sort();
}

/**
 * Sum the gzip size of the initial files by kind.
 *
 * @param root - The build directory.
 * @param files - Initial files.
 * @returns Gzip bytes of JavaScript and CSS.
 */
export function measure(root, files) {
	let js = 0;
	let css = 0;
	for (const file of files) {
		const size = gzipSync(readFileSync(join(root, file)), { level: 9 }).length;
		if (file.endsWith('.css')) css += size;
		else js += size;
	}
	return { js_gzip: js, css_gzip: css };
}

/**
 * Find the lazily loaded chunks by the marks their modules export (`vakr-lazy-chunk:<name>`).
 *
 * @param root - The build directory.
 * @returns Chunk paths by name.
 */
export function lazyChunks(root) {
	const dir = join(root, '_app', 'immutable', 'chunks');
	const found = {};
	for (const name of readdirSync(dir).filter((n) => n.endsWith('.js'))) {
		const text = readFileSync(join(dir, name), 'utf8');
		for (const match of text.matchAll(/vakr-lazy-chunk:([a-z]+)/g)) {
			if (match[1]) found[match[1]] = `_app/immutable/chunks/${name}`;
		}
	}
	return found;
}

/**
 * Inspect the build and write the manifest; exit 1 on a problem.
 *
 * @param root - The build directory.
 */
function main(root) {
	const pages = readdirSync(root).filter((name) => name.endsWith('.html'));
	const hashes = new Set();
	const problems = [];
	for (const page of pages) {
		const found = inspectHtml(readFileSync(join(root, page), 'utf8'));
		found.scripts.forEach((body) => hashes.add(scriptHash(body)));
		found.problems.forEach((p) => problems.push(`${page}: ${p}`));
	}
	const index = readFileSync(join(root, 'index.html'), 'utf8');
	const files = initialFiles(index);
	const initial = { ...measure(root, files), files };
	const lazy = lazyChunks(root);
	if (!lazy.echarts) problems.push('the ECharts chunk was not found');
	for (const [name, file] of Object.entries(lazy)) {
		if (files.includes(file)) problems.push(`the lazy ${name} chunk is in the initial bundle`);
	}
	if (initial.js_gzip > JS_BUDGET) problems.push(`initial JS ${initial.js_gzip} B > ${JS_BUDGET}`);
	if (initial.css_gzip > CSS_BUDGET)
		problems.push(`initial CSS ${initial.css_gzip} B > ${CSS_BUDGET}`);
	const bundled = JSON.parse(readFileSync('.svelte-kit/vakr-bundled.json', 'utf8'));
	if (bundled.length === 0) problems.push('the bundled package list is empty');
	if (problems.length > 0) {
		console.error(`csp-manifest: refused: ${problems.join('; ')}`);
		process.exit(1);
	}
	const manifest = { format: 1, script_hashes: [...hashes].sort(), initial, lazy, bundled };
	writeFileSync(join(root, 'vakr-web.json'), JSON.stringify(manifest, null, 2) + '\n');
	const kb = (n) => (n / 1024).toFixed(1);
	console.log(
		`csp-manifest: ${hashes.size} inline script hash(es); initial JS ${kb(initial.js_gzip)} KiB, ` +
			`CSS ${kb(initial.css_gzip)} KiB gzip (budget ${kb(JS_BUDGET)} / ${kb(CSS_BUDGET)})`
	);
}

if (import.meta.url === `file://${process.argv[1]}`) main(process.argv[2] ?? 'build');
