/**
 * A Vite plugin that removes the inline `style` attribute from SvelteKit's route announcer
 * (ADR-035 decision 13; Appendix E.31).
 *
 * SvelteKit's root component renders `<div id="svelte-announcer" style="...">`,
 * the visually hidden live region that announces page titles after client
 * navigation. vakr's CSP has `style-src 'self'` with no `unsafe-inline`, so
 * the browser refuses that attribute and reports a violation. This plugin
 * deletes the attribute before Svelte compiles the component; `app.css`
 * hides the announcer by its id instead. If SvelteKit changes the markup,
 * the build fails, so the workaround is reviewed instead of silently lost.
 *
 * @packageDocumentation
 */
import type { Plugin } from 'vite';

const ROOT = /@sveltejs[\\/]kit[\\/]src[\\/]runtime[\\/]components[\\/]root\.svelte$/;
const STYLE = /(id="svelte-announcer"[^>]*?)\s+style="[^"]*"/;

/**
 * Remove the announcer's style attribute from SvelteKit's root component source.
 *
 * @param code - The source of `root.svelte`.
 * @returns The source without the attribute.
 * @throws Error when the announcer markup is not found.
 */
export function stripAnnouncerStyle(code: string): string {
	if (!STYLE.test(code)) {
		throw new Error(
			'vite-csp-announcer: SvelteKit root.svelte no longer has the styled announcer; ' +
				'review the CSP workaround (Appendix E.31)'
		);
	}
	return code.replace(STYLE, '$1');
}

/**
 * Return the Vite plugin.
 *
 * @returns The plugin, run before the Svelte compiler.
 */
export function cspAnnouncer(): Plugin {
	return {
		name: 'vakr-csp-announcer',
		enforce: 'pre',
		transform(code: string, id: string) {
			const file = id.split('?')[0] ?? id;
			return ROOT.test(file) ? { code: stripAnnouncerStyle(code), map: null } : null;
		}
	};
}
