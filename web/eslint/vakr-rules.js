/**
 * vakr's own ESLint rules for the web UI (ADR-035 decisions 10, 13, 19; blueprint 17.3).
 *
 * - `require-module-doc`: every module opens with its documentation, as
 *   every Python module does: a TSDoc block with `@packageDocumentation` in
 *   `.ts` and `.js` files, an `<!-- @component ... -->` comment in `.svelte`
 *   files (the comment Svelte's tooling shows for a component).
 * - `no-inline-style`: no `style` attribute in Svelte markup. The CSP
 *   refuses inline styles (`style-src 'self'`); use a class or a `style:`
 *   directive, which sets the property through the CSSOM.
 *
 * @packageDocumentation
 */

/**
 * Report a module that does not start with its documentation comment.
 *
 * @param context - The ESLint rule context.
 * @returns The rule's visitors.
 */
function checkModuleDoc(context) {
	const source = context.sourceCode;
	return {
		Program(node) {
			const text = source.text.trimStart();
			if (context.filename.endsWith('.svelte')) {
				const first = /^<!--([\s\S]*?)-->/.exec(text);
				if (!first || !/@component/.test(first[1] ?? '')) {
					context.report({ node, messageId: 'svelte' });
				}
				return;
			}
			const first = /^\/\*\*([\s\S]*?)\*\//.exec(text);
			if (!first || !/@packageDocumentation/.test(first[1] ?? '')) {
				context.report({ node, messageId: 'module' });
			}
		}
	};
}

/**
 * Report a `style` attribute in Svelte markup.
 *
 * @param context - The ESLint rule context.
 * @returns The rule's visitors.
 */
function checkInlineStyle(context) {
	return {
		SvelteAttribute(node) {
			if (node.key?.name === 'style') context.report({ node, messageId: 'style' });
		}
	};
}

export default {
	meta: { name: 'vakr' },
	rules: {
		'require-module-doc': {
			meta: {
				type: 'suggestion',
				docs: { description: 'Every module starts with its documentation (17.3).' },
				messages: {
					module: 'Start the module with a /** ... @packageDocumentation */ comment.',
					svelte: 'Start the component with an <!-- @component ... --> comment.'
				},
				schema: []
			},
			create: checkModuleDoc
		},
		'no-inline-style': {
			meta: {
				type: 'problem',
				docs: { description: 'The CSP refuses inline styles (ADR-035 #13).' },
				messages: { style: 'No style attribute: the CSP blocks it. Use a class or style:.' },
				schema: []
			},
			create: checkInlineStyle
		}
	}
};
