# ADR-035: Web presentation layer: a static SvelteKit app with uPlot, ECharts, and TanStack

| Field | Value |
|---|---|
| Status | Accepted 2026-10-21 (owner: SvelteKit, uPlot, ECharts, and TanStack, keeping FastAPI) |
| Date | 2026-10-21 |
| Supersedes | The presentation half of ADR-013 (Jinja templates, HTMX, ECharts as the time-series default). ADR-013's FastAPI and Textual parts stand. |
| Decides | How the web UI is built, packaged, served, secured, typed, and tested; which chart and data libraries it uses |
| Requirements | Blueprint 2 (web stack row), 8.18 (UI-001 to UI-017), 12, 15.4, 19.7 (WEB-T01 to WEB-T11), 27.4, 27.8 (M4); SEC-013, SEC-017, UI-011, UI-012, TOOLS-001, QG-008 |

## Context

ADR-013 proposed a server-rendered UI: Jinja templates, HTMX for partial updates, and Apache ECharts for every graph. The owner never approved it. Milestone M4 then built JSON API v1 (`vakr-api`, Appendix E.30) as a complete, typed contract with a checked-in OpenAPI document. The TUI and CLI will also use that API, over the local socket.

The owner asked for an alternative: SvelteKit for the pages, keeping FastAPI; uPlot as the default chart; ECharts for specialty visualizations; and TanStack for server data and large tables. The trade-offs considered were these.

**For the alternative:**

* The API already exists, so a client-side app adds no server code. One contract serves the web UI, the TUI, and future integrations.
* uPlot draws tens of thousands of points per series quickly, in a small bundle, which suits 400-day graphs and the interface graph cards of UI-016.
* TanStack Query gives caching, deduplicated requests, and bounded background refresh (UI-013: auto-refresh without page flashing). TanStack Table and Virtual handle sortable, paginated, and long tables (12.9).
* TypeScript types generated from the OpenAPI document catch contract drift at build time.

**Against it:**

* A second language and toolchain (TypeScript, Node, npm) enter the build, the release, and CI.
* The npm dependency tree is large at build time. Only the bundled output ships, but the supply chain still needs pinning, auditing, and licence checks.
* Server-rendered HTML gives keyboard order, focus, and semantics almost for free; a client-side app has to build and test them (UI-011).
* uPlot has one maintainer, and SvelteKit's major versions move quickly.
* SvelteKit's static output contains a small inline bootstrap script, so the CSP needs hashes rather than a plain `script-src 'self'`.

## Decision

### Build and serving

1. **A static SvelteKit build.** The app lives in `web/` at the repository root. It is built with `@sveltejs/adapter-static` as a single-page app (`ssr = false`, one fallback page), using Svelte 5 runes and TypeScript in strict mode. There is no Node server and no server-side rendering: Node is a build-time tool only.
2. **Packaged with vakr.** The package build runs `npm ci` and the production build. The output is installed under `/opt/vakr/share/web/` (ADR-025), and nothing outside the package changes.
3. **Same origin.** `vakr-api` serves the built files outside `/api/` through the `create_app(context, web=...)` hook (E.30 decision 14), so the reverse proxy forwards a single upstream. The session cookie, the CSRF header, and the CSP all stay same-origin; CORS stays off. Unknown paths outside `/api/` get the app's fallback page, and unknown paths under `/api/` keep the JSON error envelope.
4. **Caching.** Content-hashed files under `/_app/immutable/` are served `Cache-Control: public, max-age=31536000, immutable`; HTML is served `no-cache`.

### Data and charts

5. **Typed API client.** TypeScript types are generated from `docs/api/openapi.json` with `openapi-typescript`, and requests go through `openapi-fetch`. The generated file is checked in, and CI fails if it differs from a fresh generation (the counterpart of API-T01).
6. **Server state with TanStack Query.** Polling uses `refetchInterval`, which replaces the HTMX polling named in 27.4; Server-Sent Events stay deferred. Only idempotent GETs are retried, at most twice. Writes send `X-CSRF-Token` and never retry. Refresh pauses while the tab is hidden.
7. **Tables with TanStack Table** use the API's server-side paging and cursors (API-005). **TanStack Virtual** is used only where a page renders a long list that is already bounded.
8. **uPlot is the default chart** for time series: traffic, packets, errors, utilization, RTT summary, and loss.
9. **ECharts only where uPlot lacks the visualization.** It is imported through its modular API so the build can drop unused parts, and it is loaded lazily as a separate chunk, so pages without such a chart never download it. Each ECharts use is listed in Appendix E.31 with the reason uPlot was not enough.
10. **Charts sit behind vakr's own components** (`web/src/lib/charts/`). Pages never import uPlot or ECharts directly, so either library can be replaced in one place. This mitigates the single-maintainer risk.
11. **Missing data stays missing.** `null` points are gaps, never zero (invariant 1, UI-010). Every graph has a tabular summary and accessible exact values (12.6, 12.8).

### Security (15.4, SEC-013, UI-012)

12. **No external runtime requests.** Every script, style, font, and image is served from the package. There are no CDNs and no remote fonts.
13. **CSP set as a header by `vakr-api`.** It has no `unsafe-inline` and no `unsafe-eval`. Scripts may come only from `'self'` plus the SHA-256 hashes of the build's inline bootstrap, which the build writes to a manifest that the server reads at startup. The policy also sets `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'self'`, and `connect-src 'self'`. The other E.30 security headers apply to the pages too.
14. **Escaping.** Svelte escapes interpolated text. `{@html}` is forbidden by a lint rule, which takes the place of 15.4's "HTML auto-escaping remains enabled".
15. **Public pages.** When public dashboards arrive (deferred, 27.4), they will use the separate `/public/v1` endpoints and their own allowlisted views (12.7).

### Supply chain (SEC-017, QG-008, TOOLS-001)

16. **Pinned.** `web/package-lock.json` is committed, and builds use `npm ci --ignore-scripts`. The Node version is pinned in `web/.node-version` and in `engines`.
17. **Audited.** CI runs `npm audit` for runtime and build dependencies, and a licence check against an allowlist compatible with Apache-2.0. Bundled front-end packages are listed in `packaging/copyright` and in the SBOM.
18. **Kept current.** `tools/check-updates` reports newer npm packages and Node releases, replacing the "vendored front-end assets (ECharts, HTMX)" item.

### Code rules and tests

19. **The same rules as the Python code.**
    * Source files are at most 300 lines and functions at most 50 lines, enforced by ESLint's `max-lines` and `max-lines-per-function`.
    * Every module and exported function has a TSDoc comment stating its purpose, network context where it applies, and an example where it helps.
    * ESLint, `svelte-check`, and Prettier run in `make check`.
20. **Tests.**
    * Vitest covers unit logic: units, gap handling, ordering, and state mapping.
    * Playwright covers the pages against a real `vakr api serve` on seeded databases (WEB-T01 to WEB-T07, WEB-T11).
    * `@axe-core/playwright` provides the automated part of WEB-T08 and WEB-T09.
    * WEB-T10 asserts that no request leaves the origin and that the CSP and headers are as specified.

### Performance (12.9)

21. **What the budget measures.** The page shell is static, so 12.9's "first HTML within 1 second p95" is read as "the page's primary data is shown within 1 second p95". That includes the API answer, so the API's bounds (E.28, E.30) still decide it. The bounds for initial JS and CSS size, graph cards per page, and refresh intervals are in Appendix E.31.

## Consequences

* The web UI is a client of API v1. Anything the UI needs that the API lacks is added to the API, with its OpenAPI document, first.
* Node joins the build host's toolchain: the developer workstation, CI, and the release machine. The release preflight checks it. Production hosts never need Node.
* `make package` and the release take longer by the time of the front-end build.
* Blueprint 2 (web stack row), 15.4, 27.4, TOOLS-001, and Appendix C now point here; ADR-013 is superseded in part.
