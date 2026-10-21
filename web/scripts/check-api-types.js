/**
 * Fail when `src/lib/api/schema.d.ts` differs from a fresh generation (ADR-035 decision 5).
 *
 * The web UI's request and answer types are generated from the checked-in
 * OpenAPI document of API v1 (`docs/api/openapi.json`, itself checked
 * against the code by API-T01). This script runs the same generator into a
 * temporary file and compares bytes, so contract drift fails CI.
 *
 * Usage: `node scripts/check-api-types.js` (`npm run check:api`); fix with
 * `npm run gen:api`.
 *
 * @packageDocumentation
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('..', import.meta.url));
const spec = join(here, '..', 'docs', 'api', 'openapi.json');
const target = join(here, 'src', 'lib', 'api', 'schema.d.ts');
const cli = join(here, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');

/**
 * Generate the types into a temporary directory and return them.
 *
 * @returns The generated text.
 */
function generate() {
	const dir = mkdtempSync(join(tmpdir(), 'vakr-api-types-'));
	try {
		const out = join(dir, 'schema.d.ts');
		execFileSync(process.execPath, [cli, spec, '--output', out], { stdio: 'pipe' });
		return readFileSync(out, 'utf8');
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
}

const fresh = generate();
const current = readFileSync(target, 'utf8');
if (fresh !== current) {
	console.error(
		'check-api-types: src/lib/api/schema.d.ts is out of date with docs/api/openapi.json; ' +
			'run `npm run gen:api` in web/'
	);
	process.exit(1);
}
console.log('check-api-types: src/lib/api/schema.d.ts matches docs/api/openapi.json');
