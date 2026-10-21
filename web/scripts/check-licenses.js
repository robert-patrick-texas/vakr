/**
 * Check every npm package's licence against an allowlist compatible with Apache-2.0
 * (SEC-017, QG-008, UI-012; ADR-035 decision 17).
 *
 * - Every locked package (build tools included) must carry a licence from
 *   `ALLOWED`. An SPDX expression passes when an `OR` branch passes and every
 *   `AND` term passes.
 * - Packages that end up in the browser build (`bundled` in
 *   `build/vakr-web.json`) must carry a permissive licence from `BUNDLED`,
 *   and `packaging/copyright` must name each of them, so the .deb's copyright
 *   file always covers what it ships.
 *
 * Usage: `node scripts/check-licenses.js` after `npm run build`.
 *
 * @packageDocumentation
 */
import { readFileSync } from 'node:fs';

/** Permissive licences that may be bundled into the shipped app. */
export const BUNDLED = new Set([
	'MIT',
	'MIT-0',
	'ISC',
	'BSD-2-Clause',
	'BSD-3-Clause',
	'Apache-2.0',
	'0BSD'
]);
/** Licences allowed for build and test tools, which are never shipped. */
export const ALLOWED = new Set([
	...BUNDLED,
	'BlueOak-1.0.0',
	'CC0-1.0',
	'CC-BY-3.0',
	'CC-BY-4.0',
	'MPL-2.0',
	'Python-2.0',
	'Unlicense'
]);

/**
 * Decide whether an SPDX expression is allowed.
 *
 * @param expression - Such as `MIT` or `(MIT OR CC0-1.0)`.
 * @param allowed - Accepted licence identifiers.
 * @returns True when some `OR` branch has only accepted `AND` terms.
 */
export function licenseAllowed(expression, allowed) {
	if (!expression) return false;
	const text = expression.replace(/[()]/g, ' ').trim();
	return text
		.split(/\s+OR\s+/)
		.some((branch) => branch.split(/\s+AND\s+/).every((term) => allowed.has(term.trim())));
}

/**
 * Map each locked package to its licence.
 *
 * @param lock - Parsed `package-lock.json`.
 * @returns Entries of name, version, licence, and whether it is a development package.
 */
export function lockedPackages(lock) {
	return Object.entries(lock.packages ?? {})
		.filter(([path]) => path.includes('node_modules/'))
		.map(([path, entry]) => ({
			name: path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length),
			version: entry.version,
			license: entry.license,
			dev: Boolean(entry.dev || entry.devOptional)
		}));
}

/**
 * Find every licence problem.
 *
 * @param lock - Parsed `package-lock.json`.
 * @param bundled - Names of the packages in the browser build.
 * @param copyright - The text of `packaging/copyright`.
 * @returns Problems, one sentence each.
 */
export function licenseProblems(lock, bundled, copyright) {
	const packages = lockedPackages(lock);
	const problems = packages
		.filter((p) => !licenseAllowed(p.license, ALLOWED))
		.map((p) => `${p.name}@${p.version}: licence ${p.license ?? 'missing'} is not allowed`);
	for (const name of bundled) {
		const entry = packages.find((p) => p.name === name);
		if (!entry) problems.push(`${name}: bundled but not in package-lock.json`);
		else if (!licenseAllowed(entry.license, BUNDLED)) {
			problems.push(`${name}: licence ${entry.license} may not be bundled`);
		}
		if (!copyright.includes(name))
			problems.push(`${name}: bundled but not named in packaging/copyright`);
	}
	return problems;
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'));
	const { bundled } = JSON.parse(readFileSync('build/vakr-web.json', 'utf8'));
	const copyright = readFileSync('../packaging/copyright', 'utf8');
	const problems = licenseProblems(lock, bundled, copyright);
	if (problems.length > 0) {
		console.error(`check-licenses: ${problems.length} problem(s):\n  ${problems.join('\n  ')}`);
		process.exit(1);
	}
	console.log(
		`check-licenses: ${lockedPackages(lock).length} locked packages allowed; ` +
			`${bundled.length} bundled packages permissive and named in packaging/copyright`
	);
}
