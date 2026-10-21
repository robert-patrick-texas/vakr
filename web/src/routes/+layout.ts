/**
 * Page options for every route: a client-only single-page app (ADR-035 decision 1).
 *
 * `ssr = false` means the fallback page is an empty shell that the browser
 * fills from API v1; `prerender = false` means nothing is rendered at build
 * time, so no page ever embeds monitoring data.
 *
 * @packageDocumentation
 */
export const ssr = false;
export const prerender = false;
