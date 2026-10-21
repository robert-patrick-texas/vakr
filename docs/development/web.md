# Developing the web UI

The web UI is a static SvelteKit app in `web/`, built to plain files that `vakr-api` serves (ADR-035, [Appendix E.31](../../tool-blueprint.md#e31-web-ui-m4)). It is a client of API v1 only: every request goes through the generated, typed client, and anything a page needs that the API lacks is added to the API first. This page covers the toolchain, the build, the checks, the tests, and how to run it against a local API.

- [Toolchain](#toolchain)
- [Layout](#layout)
- [Build](#build)
- [Running it during development](#running-it-during-development)
- [API types](#api-types)
- [Checks in make check](#checks-in-make-check)
- [End-to-end tests](#end-to-end-tests)
- [How the API serves the build](#how-the-api-serves-the-build)
- [Rules for new code](#rules-for-new-code)
- [Updating dependencies](#updating-dependencies)

## Toolchain

| Tool | Version | Where it is pinned |
|---|---|---|
| Node.js | 24.22.0 (24 LTS), exact | `web/.node-version` (read by CI's `setup-node` and the release preflight), `engines` in `web/package.json` (`engine-strict` in `web/.npmrc`), and the builder image in `packaging/Dockerfile`, which a test keeps equal to `web/.node-version` |
| npm | 11.19 or newer | `engines` |
| Packages | exact versions | `web/package.json` (no ranges) and `web/package-lock.json` |

Node and npm are build-time tools only. Production hosts never need them: the package carries the built files. Install dependencies from the lockfile, never running packages' install scripts:

```console
$ make web-install          # cd web && npm ci --ignore-scripts
```

TypeScript stays on the 6.x line: TypeScript 7 is out, but SvelteKit 3.1, svelte-check, and typescript-eslint accept only up to 6.x (E.31). `openapi-typescript` declares TypeScript 5 as its peer; `overrides` in `package.json` lets it use the project's TypeScript 6, which it runs only to print the generated file.

## Layout

| Path | What |
|---|---|
| `web/src/routes/` | One folder per page (`+page.svelte`); `+layout.svelte` holds the query client, the session gate, the navigation, and the live region. |
| `web/src/lib/api/` | The generated types (`schema.d.ts`), short aliases (`types.ts`), the `openapi-fetch` client with the CSRF header and the 401 hook (`client.ts`), failures (`errors.ts`), and the session (`session.svelte.ts`). |
| `web/src/lib/query/` | TanStack Query options per endpoint (`reads*.ts`) and the refresh and retry policy (`policy.ts`). |
| `web/src/lib/state/` | Pure logic: UI-014 page states, URL filters, graph ranges, cursor paging, kiosk options and the rotation schedule (`kiosk.ts`). |
| `web/src/lib/kiosk/` | The kiosk dashboards (UI-013): what each reads (`boards.ts`), their components, and the full-screen notices. Shared components render links through `Link.svelte`, which is plain text under the kiosk layout. |
| `web/src/lib/charts/` | The only code that imports uPlot or ECharts (ADR-035 #10): data shaping (`series.ts`, `latency.ts`), options, and the chart components. |
| `web/src/lib/components/` | Tables (TanStack Table), status badges, readings, filters, panels. |
| `web/scripts/` | Build helpers: the CSP manifest and size budget, the API type drift check, the licence check, and two Vite plugins. |
| `web/e2e/` | Playwright tests. |
| `tests/e2e/` (repository root) | The Python harness that seeds a fresh install and runs `vakr api serve` for those tests. |

Modules import each other through the `#lib/...` subpath (package.json `imports`; SvelteKit 3 removed `$lib`), with explicit `.ts` and `.svelte` extensions.

## Build

```console
$ make web-build            # cd web && npm run build
```

`npm run build` runs `vite build` (adapter-static: `index.html` as the single-page fallback, content-hashed files under `_app/immutable/`, precompressed `.br` and `.gz` copies) and then `scripts/csp-manifest.js`, which writes `build/vakr-web.json`:

- `script_hashes`: the SHA-256 of SvelteKit's inline bootstrap script. `vakr-api` adds them to `script-src`; there is no `unsafe-inline` or `unsafe-eval`.
- `initial`: the gzip size of the JavaScript and CSS the fallback page loads before the app runs. The build fails above the E.31 budget (120 KiB JavaScript, 16 KiB CSS).
- `lazy`: chunks loaded on demand, found by the `vakr-lazy-chunk:<name>` mark their module exports; the build fails if one is in the initial bundle (the ECharts chunk must never be).
- `bundled`: the npm packages in the client build, recorded by `scripts/vite-bundled-packages.ts`.

The build also fails if any HTML carries an inline style or event handler. SvelteKit's route announcer has an inline `style` attribute, which the CSP would refuse; `scripts/vite-csp-announcer.ts` removes it at build time (the stylesheet hides the announcer instead) and fails the build if SvelteKit changes that markup.

`version.name` in `vite.config.ts` comes from `SOURCE_DATE_EPOCH`, so two package builds of one commit produce identical files (PKG-004). The release number is never written into the build: the header reads it from `GET /api/v1/version` (VER-001), and `web/package.json` carries the fixed placeholder version `0.0.0` with `"private": true`.

## Running it during development

Run the API with the seeded e2e fleet (or your own install), then the Vite dev server, which proxies `/api/` to it, so cookies and CSRF stay same-origin:

```console
$ export VAKR_E2E_ADMIN_PASSWORD=$(openssl rand -base64 18)    # never commit or print it
$ uv run python -m tests.e2e.serve --port 8080 --web-dir web/build --fixtures-out /tmp/fx.json &
$ cd web && npm run dev                                         # http://127.0.0.1:5173/
```

Sign in as `e2e-admin` with that password. `VAKR_API_URL` points the dev proxy at another API (default `http://127.0.0.1:8080`). The dev server does not apply the production CSP; check CSP-sensitive changes against a build served by `vakr api serve`.

To have `vakr api serve` serve your build, set `VAKR_WEB_DIR=web/build` (the development and test override, E.31); without it the API serves `/opt/vakr/share/web` under `VAKR_ROOT`. The build is read once at start: restart the API after rebuilding.

## API types

`web/src/lib/api/schema.d.ts` is generated from [`docs/api/openapi.json`](../api/openapi.json) with `openapi-typescript` and checked in. After changing a route or schema:

```console
$ make openapi                     # regenerate docs/api/openapi.json (API-T01 checks it)
$ cd web && npm run gen:api        # regenerate the TypeScript types
```

`npm run check:api` (part of `make check`) regenerates into a temporary file and fails when the checked-in types differ, so the client can never drift from the contract. The generated file is excluded from ESLint and Prettier; the drift check is its gate.

## Checks in make check

`make check` runs `make web-check` after the Python gates:

| Check | Command | Enforces |
|---|---|---|
| ESLint | `npm run lint` | `max-lines` 300 and `max-lines-per-function` 50 (blueprint 17.2); a TSDoc block with `@packageDocumentation` opening every `.ts`/`.js` module and an `<!-- @component -->` comment opening every component (17.3); TSDoc on every exported function (17.4); Svelte's raw-HTML tag forbidden (15.4); no `style` attributes (CSP); no chart library imported outside `src/lib/charts/` (ADR-035 #10) |
| svelte-check | `npm run check:svelte` | TypeScript strict across components and modules, warnings fail |
| Prettier | `npm run format:check` | Formatting (`npm run format` fixes it) |
| Vitest | `npm test` | Unit logic: gaps and alignment, latency smoke, page states, filters, ranges and zones, units, retry policy, the build scripts |
| API types | `npm run check:api` | No drift from `docs/api/openapi.json` |
| Build | `npm run build` | The CSP manifest, the size budget, the lazy ECharts chunk |
| Licences | `npm run licenses` | Every locked package under an allowlist compatible with Apache-2.0; bundled packages under the permissive subset and named in `packaging/copyright` |

`make web-audit` runs `npm audit` for all packages and then the runtime ones; CI fails on high or critical findings.

## End-to-end tests

```console
$ cd web && npx playwright install chromium     # once; CI adds --with-deps
$ make web-e2e                                  # builds, then runs Playwright
```

`web/playwright.config.ts` starts two harness servers from the repository root, `python -m tests.e2e.serve`, each on a freshly seeded SQLite install served by a real `vakr api serve`: `normal` (the fleet) and `no-telemetry` (no telemetry database). The config generates both accounts' passwords for the run and passes them through the environment; they are never written to disk or printed. The harness writes the seeded UUIDs to `web/test-results/.fixtures/<scenario>.json`.

| File | Covers |
|---|---|
| `devices.spec.ts` | WEB-T01 order, filters, browsing (UI-009), sort announcements |
| `device-detail.spec.ts` | WEB-T02, WEB-T03, WEB-T05 (smoke and loss against the API's data), WEB-T11 (card order, 12 per page, graphs only when visible, 120 points) |
| `interface-detail.spec.ts` | WEB-T04 (gaps and markers, never zero), WEB-T06 (native versus rollup, the zone), WEB-T03 (filters kept on the way back), the lazy ECharts chunk |
| `states.spec.ts` | WEB-T07: partial, no telemetry, no answer, database unavailable, stale after a failed refresh, empty, denied; focus kept across a refresh |
| `a11y.spec.ts` | WEB-T08 (axe, WCAG 2.0 to 2.2 A/AA, light and dark, reduced motion; keyboard path), WEB-T09 (icon and words, no icon for two colours) |
| `security.spec.ts` | WEB-T10: no request leaves the origin, no CSP violation on any page, CSP and 15.4 headers, cache headers, envelopes |
| `auth.spec.ts`, `navigation.spec.ts`, `reviews.spec.ts` | Sign-in and an ended session, navigation and search (UI-017), the dashboards, collector health, the admin's review decision with CSRF |

On a host without Chromium's system libraries (and without root to install them), run the browser in the Playwright container and point the tests at it; the test servers stay on the host:

```console
$ docker run --rm --network host --init mcr.microsoft.com/playwright:v1.64.1-noble \
    /bin/sh -c "cd /tmp && npx -y playwright@1.64.1 run-server --port 3123 --host 127.0.0.1" &
$ cd web && PW_WS_ENDPOINT=ws://127.0.0.1:3123/ npx playwright test
```

Keep the container's Playwright version equal to `@playwright/test` in `package.json`. `VAKR_E2E_SERVER_LOG=1` shows the servers' logs; `VAKR_E2E_PORT` moves them (default 18920 and 18921).

## How the API serves the build

`vakr.api.web` attaches the build to the FastAPI app through `create_app(context, web=...)` (E.30 #14). At start, `vakr.api.web_files` reads the build directory into memory (at most 2,000 files and 64 MiB), so no request path is ever joined to a file path. One GET/HEAD route answers everything outside `/api/`:

| Request | Answer |
|---|---|
| A file under `/_app/immutable/` | The file, `Cache-Control: public, max-age=31536000, immutable` |
| Another file of the build | The file, `Cache-Control: no-cache` with an `ETag` (304 on revalidation) |
| A missing `/_app/...` file | 404 with the JSON envelope |
| Any other path | `index.html`, 200, `no-cache`, so the client router handles deep links |
| Anything under `/api/` | Never this route: unknown API paths keep the API's 404 and 405 envelopes |

HTML carries the page CSP: the API's same-origin policy plus the bootstrap's hashes in `script-src`. A client that accepts `br` or `gzip` gets the precompressed file with `Vary: Accept-Encoding`. Without a usable build, the API logs `api.web_unavailable` once and serves the API alone.

## Rules for new code

- Fetch only through `#lib/api/client.ts` and a query in `#lib/query/`; add the endpoint to the API (route, schema, tests, `make openapi`, `npm run gen:api`) before the page uses it.
- Show every block of data through `PageStatus` with a `pageState(...)`, so loading, empty, stale, partial, denied, and unavailable are handled the same way everywhere (UI-014). Never render `null` as 0; show the API's `Reading.text` and its reason.
- Status needs colour, icon, and words: use `StatusBadge`.
- No inline `style` attributes; use classes, or `style:` directives, which the CSP allows.
- Charts only in `src/lib/charts/`; ECharts only where uPlot cannot draw the visualization, imported with `import()` and listed in E.31.
- Tables of server pages use `DataTable` (TanStack Table) with `aria-sort` and an announcement for every sortable header.
- A file stays at or below 300 lines and a function at or below 50, as in Python.

## Updating dependencies

`uv run tools/check-updates/check_updates.py` lists newer npm packages and Node releases beside the Python ones ([updates](updates.md)). Update with `npm install --save-exact --ignore-scripts <package>@<version>` in `web/`, run `make check` and `make web-e2e`, and update `packaging/copyright` if the set of bundled packages changes (the licence check names any that are missing). When Node changes, change `web/.node-version` and the `node` stage of `packaging/Dockerfile` together (and `engines` for a new floor or major line).
