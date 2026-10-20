# Quality gates (blueprint section 17.8). CI runs `make check`; run it locally
# before pushing. Every target runs through uv so the locked toolchain is used.
SITE ?= _site
TY_PATHS := --extra-search-path tools/md2html --extra-search-path tools/snmpsim

.PHONY: check fmt lint types test loc version docs docs-check

check: lint types test loc version docstrings docs-check

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

version:
	uv run python tools/check-version/check_version.py --allow CHANGELOG.md --allow uv.lock --allow src/vakr/defaults/options.toml

# Docstring sections and Args entries (QG-003); rules in [tool.check-docstrings].
.PHONY: docstrings
docstrings:
	uv run python tools/check-docstrings/check_docstrings.py src tests tools

# Opt-in SNMP load check: the 100-device MVP fleet from snmpsim within one 60 s
# interval (blueprint 4.2, 27.2). Takes about 30 s; not part of `make check`.
.PHONY: snmp-load
snmp-load:
	VAKR_SNMP_LOAD=1 uv run pytest -p no:cacheprovider -s tests/simulators/snmp/test_snmp_load.py

docs:
	uv run tools/md2html/md2html.py . $(SITE) --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main --exclude .claude

docs-check:
	uv run tools/md2html/md2html.py . $(SITE) --check --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main --exclude .claude

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
