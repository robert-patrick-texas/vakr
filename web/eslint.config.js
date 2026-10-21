/**
 * ESLint configuration of the vakr web UI: the same rules as the Python code (ADR-035 #19).
 *
 * - Files at most 300 lines and functions at most 50 (blueprint 17.2).
 * - A documentation comment opens every module, and every exported function
 *   has TSDoc (17.3, 17.4).
 * - Svelte's raw-HTML tag is forbidden, which keeps its escaping on (15.4, ADR-035 #14).
 * - No inline styles (the CSP refuses them), and pages never import a chart
 *   library directly: charts sit behind `src/lib/charts/` (ADR-035 #10).
 *
 * The generated API types (`src/lib/api/schema.d.ts`) are excluded: they are
 * checked byte-for-byte against a fresh generation instead (`npm run check:api`).
 *
 * @packageDocumentation
 */
import js from '@eslint/js';
import jsdoc from 'eslint-plugin-jsdoc';
import svelte from 'eslint-plugin-svelte';
import tsdoc from 'eslint-plugin-tsdoc';
import globals from 'globals';
import ts from 'typescript-eslint';
import vakr from './eslint/vakr-rules.js';

const CHART_LIBRARIES = {
	patterns: [
		{
			group: ['uplot', 'uplot/*', 'echarts', 'echarts/*', 'zrender', 'zrender/*'],
			message: 'Use the components in src/lib/charts/ (ADR-035 #10).'
		}
	]
};

export default ts.config(
	{
		ignores: [
			'build/',
			'.svelte-kit/',
			'node_modules/',
			'playwright-report/',
			'test-results/',
			'src/lib/api/schema.d.ts'
		]
	},
	js.configs.recommended,
	...ts.configs.strict,
	...svelte.configs.recommended,
	...svelte.configs.prettier,
	jsdoc.configs['flat/recommended-typescript-error'],
	{
		languageOptions: { globals: { ...globals.browser } },
		plugins: { tsdoc, vakr },
		settings: {
			jsdoc: {
				mode: 'typescript',
				tagNamePreference: { packageDocumentation: 'packageDocumentation' }
			}
		},
		rules: {
			'max-lines': ['error', { max: 300, skipBlankLines: false, skipComments: false }],
			'max-lines-per-function': [
				'error',
				{ max: 50, skipBlankLines: false, skipComments: false, IIFEs: true }
			],
			'svelte/no-at-html-tags': 'error',
			// vakr is always served at the origin root (base path '', ADR-035 #3); links are
			// built by src/lib/links.ts, so resolve() would add nothing.
			'svelte/no-navigation-without-resolve': 'off',
			'tsdoc/syntax': 'error',
			'vakr/require-module-doc': 'error',
			'vakr/no-inline-style': 'error',
			'jsdoc/require-jsdoc': [
				'error',
				{
					publicOnly: true,
					require: {
						FunctionDeclaration: true,
						ArrowFunctionExpression: true,
						FunctionExpression: true
					}
				}
			],
			'jsdoc/check-tag-names': ['error', { definedTags: ['packageDocumentation', 'component'] }],
			'jsdoc/require-returns': ['error', { publicOnly: true }],
			'jsdoc/require-param': ['error', { exemptedBy: ['inheritdoc'] }],
			'jsdoc/tag-lines': 'off',
			'jsdoc/require-throws-type': 'off',
			'no-restricted-imports': ['error', CHART_LIBRARIES]
		}
	},
	{
		files: ['**/*.svelte', '**/*.svelte.ts'],
		languageOptions: { parserOptions: { parser: ts.parser, extraFileExtensions: ['.svelte'] } }
	},
	{
		files: ['src/lib/charts/**'],
		rules: { 'no-restricted-imports': 'off' }
	},
	{
		files: ['src/**'],
		rules: { 'no-restricted-globals': ['error', { name: 'process', message: 'Browser code.' }] }
	},
	{
		files: ['scripts/**', 'eslint/**', 'e2e/**', '*.config.*', 'eslint.config.js'],
		languageOptions: { globals: { ...globals.node } }
	}
);
