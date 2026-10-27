# Quality gates (blueprint section 17.8). CI runs `make check`; run it locally
# before pushing. Every target runs through uv so the locked toolchain is used.
SITE ?= _site
TY_PATHS := --extra-search-path tools/md2html --extra-search-path tools/snmpsim

.PHONY: check fmt lint types test loc version docs docs-check

check: lint types test loc version docstrings docs-check web-check

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

types:
	uv run ty check $(TY_PATHS)

test:
	uv run pytest -p no:cacheprovider --cov --cov-report=term --cov-fail-under=90

loc:
	uv run python tools/check-loc/check_loc.py src tools --exceptions docs/architecture/loc-exceptions.md

# web/package-lock.json is allowed for the same reason as uv.lock: a dependency's own
# version may equal vakr's. web/package.json carries the fixed placeholder 0.0.0.
version:
	uv run python tools/check-version/check_version.py --project vakr --allow CHANGELOG.md --allow uv.lock \
		--allow web/package-lock.json --allow src/vakr/defaults/options.toml

# Docstring sections and Args entries (QG-003); rules in [tool.check-docstrings].
.PHONY: docstrings
docstrings:
	uv run python tools/check-docstrings/check_docstrings.py src tests tools

# OpenAPI document of API v1 (API-008), checked in; tests/unit/api/test_api_openapi.py
# fails when it is stale. Run after changing any route, parameter, or view.
.PHONY: openapi
openapi:
	uv run vakr api openapi --output docs/api/openapi.json

# Opt-in SNMP load check: the 100-device MVP fleet from snmpsim within one 60 s
# interval (blueprint 4.2, 27.2). Takes about 30 s; not part of `make check`.
.PHONY: snmp-load
snmp-load:
	VAKR_SNMP_LOAD=1 uv run pytest -p no:cacheprovider -s tests/simulators/snmp/test_snmp_load.py

# Generated web output (builds and test reports) is never published.
DOCS_EXCLUDE := --exclude .claude --exclude web/build --exclude web/.svelte-kit \
	--exclude web/test-results --exclude web/playwright-report

docs:
	uv run tools/md2html/md2html.py . $(SITE) --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main $(DOCS_EXCLUDE)

docs-check:
	uv run tools/md2html/md2html.py . $(SITE) --check --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main $(DOCS_EXCLUDE)

# Web UI (ADR-035): a static SvelteKit app in web/. Node and npm are build-time
# tools only (web/.node-version); `make check` runs the web gates as well:
# ESLint (line and function limits, TSDoc, no raw HTML, no inline styles),
# svelte-check, Prettier, Vitest, the OpenAPI type drift check, the production
# build (CSP manifest and the E.31 size budget), and the licence allowlist.
WEB_STAMP := web/node_modules/.package-lock.json
.PHONY: web-install web-check web-build web-e2e web-audit

$(WEB_STAMP): web/package.json web/package-lock.json
	cd web && npm ci --ignore-scripts

web-install: $(WEB_STAMP)

web-check: $(WEB_STAMP)
	cd web && npm run lint && npm run check:svelte && npm run format:check && npm test \
		&& npm run check:api && npm run build && npm run licenses

web-build: $(WEB_STAMP)
	cd web && npm run build

# End-to-end tests (WEB-T01..T11) in Chromium against a real `vakr api serve` on
# seeded SQLite databases (tests/e2e). Install the browser once with
# `cd web && npx playwright install chromium` (CI adds --with-deps). Where
# Chromium's system libraries are missing, run the browser in the Playwright
# container and set PW_WS_ENDPOINT (docs/development/web.md).
web-e2e: web-build
	cd web && npx playwright test

# Known vulnerabilities in every npm package, then in the bundled runtime ones
# (QG-008, SEC-017): high or critical fails.
web-audit: $(WEB_STAMP)
	cd web && npm audit --audit-level=high && npm audit --omit=dev --audit-level=high

# Toolchain and dependency currency (TOOLS-001, TOOLS-002); run before every
# release. Informational by default; UPDATES_FLAGS=--fail-on-outdated gates.
UPDATES_FLAGS ?=
TY_PATHS += --extra-search-path tools/check-updates

# Known-vulnerability scan of every locked runtime and development dependency
# (QG-008). Justified exceptions go in AUDIT_IGNORE: one ID per line followed
# by a "# reason" comment. check-updates reports when PIP_AUDIT falls behind.
PIP_AUDIT := pip-audit==2.10.1
AUDIT_IGNORE := tools/check-updates/audit-ignore.txt

.PHONY: updates audit

updates:
	uv run tools/check-updates/check_updates.py $(UPDATES_FLAGS)

audit:
	@awk '!/^[[:space:]]*(#|$$)/ && !/#[[:space:]]*[^[:space:]]/ \
		{ print FILENAME ":" FNR ": add a # justification"; bad = 1 } END { exit bad }' \
		$(AUDIT_IGNORE)
	@set -e; reqs=$$(mktemp); trap 'rm -f "$$reqs"' EXIT; \
	uv export --frozen --format requirements-txt --all-groups --no-emit-project \
		--quiet -o "$$reqs"; \
	uvx $(PIP_AUDIT) --strict --disable-pip --require-hashes --progress-spinner off \
		-r "$$reqs" $$(sed -e 's/#.*//' $(AUDIT_IGNORE) | awk 'NF { print "--ignore-vuln", $$1 }')

# Packaging and release (blueprint 28.6, 28.7). These need docker and git.
.PHONY: deb package-test release-dry-run github-update-test

# dist/vakr_<deb version>_amd64.deb, built in a clean ubuntu:26.04 container.
deb:
	packaging/build-deb.sh dist

# Static packaging checks, then install/remove/upgrade/purge tests in containers.
package-test:
	packaging/package-test.sh

# Every release step except tag, push, and publish; signs with a throwaway key
# when the release key is absent.
release-dry-run:
	uv run tools/release/release.py --dry-run

# After publishing: install the previous release from GitHub in a systemd
# container, self-update to this one, roll back, and update again by timer.
# FROM and TO select other releases, e.g. make github-update-test FROM=0.1.1.
github-update-test:
	FROM=$(FROM) TO=$(TO) packaging/github-update-test.sh

# Opt-in two-position system tests, MPOS-T01..T04 (blueprint 27.5): two systemd
# nodes and a simulated fleet in privileged containers, with partitions and netem.
# MPOS_QUICK=1 takes about 7 minutes with DEB given; the full run holds a 30-minute partition.
# See docs/development/two-position-tests.md. Not part of `make check`.
.PHONY: mpos-test
mpos-test:
	DEB=$(DEB) MPOS_QUICK=$(MPOS_QUICK) MPOS_FEATURES=$(MPOS_FEATURES) MPOS_DEVICES=$(MPOS_DEVICES) \
	MPOS_PARTITION_SECONDS=$(MPOS_PARTITION_SECONDS) MPOS_HOLD_SECONDS=$(MPOS_HOLD_SECONDS) \
	MPOS_KEEP=$(MPOS_KEEP) tests/system/mpos/run.sh

# Opt-in resilience system tests, MVP-ACC-06 (blueprint 27.6, Appendix E.36): one
# systemd node and a simulated fleet survive a service restart, a host reboot, the
# telemetry store unavailable, and an abrupt power loss, with every expected poll
# stored or explained. About 14 minutes with DEB given.
# See docs/development/resilience-tests.md. Not part of `make check`.
.PHONY: resilience-test
resilience-test:
	DEB=$(DEB) RESIL_DEVICES=$(RESIL_DEVICES) RESIL_OUTAGE=$(RESIL_OUTAGE) \
	RESIL_SCENARIOS=$(RESIL_SCENARIOS) RESIL_KEEP=$(RESIL_KEEP) tests/system/resilience/run.sh

# Parallel-run evidence (MVP-ACC-03, MVP-ACC-04): compare the field bundles in inbox/
# with MRTG and SmokePing and write the Markdown and JSON reports (tools/acceptance).
TY_PATHS += --extra-search-path tools/acceptance
ACCEPTANCE_OUT ?= .
.PHONY: acceptance-report
acceptance-report:
	uv run tools/acceptance/acceptance_report.py --output $(ACCEPTANCE_OUT)
