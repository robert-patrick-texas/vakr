# Quality gates (blueprint section 17.8). CI runs `make check`; run it locally
# before pushing. Every target runs through uv so the locked toolchain is used.
SITE ?= _site
TY_PATHS := --extra-search-path tools/md2html

.PHONY: check fmt lint types test loc version docs docs-check

check: lint types test loc version docs-check

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
	uv run python tools/check-version/check_version.py --allow CHANGELOG.md --allow uv.lock

docs:
	uv run tools/md2html/md2html.py . $(SITE) --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main

docs-check:
	uv run tools/md2html/md2html.py . $(SITE) --check --title vakr \
		--repo-url https://github.com/robert-patrick-texas/vakr --branch main
