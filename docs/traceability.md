# Requirements traceability

Maps each requirement to its code, tests, and documentation (blueprint section 1, Appendix B). It is updated in the same change that implements or changes a requirement. Requirements not listed here have status `Not started`.

Statuses: `Not started`, `In progress`, `Implemented`, `Verified`, `Deferred-approved`, `Not applicable-approved`.

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| VER-001 | In progress | `VERSION`, `pyproject.toml` (hatch dynamic version), `src/vakr/version.py` | `tests/unit/test_cli.py::test_version_flag_prints_installed_version` | Blueprint 28.2 | ADR-029; Go and package consumers arrive with packaging |
| VER-003 | Implemented | `tools/check-version/check_version.py` | `make version` in CI | Blueprint 28.2 | |
| OPS-001 | In progress | `src/vakr/health/`, `src/vakr/cli/cmd_check.py` | `tests/unit/test_cli.py` | Blueprint 8.22 | Check list grows with each subsystem |
| SVC-007 | In progress | `src/vakr/cli/cmd_doctor.py` | `tests/unit/test_cli.py::test_doctor_prints_one_line_per_check` | Blueprint 8.22 | Skeleton |
| DOCPUB-001 | Implemented | `tools/md2html/`, `.github/workflows/pages.yml` | `tools/md2html/test_md2html.py` | `tools/md2html/README.md` | |
| DOCPUB-002 | Implemented | `tools/md2html/md2html_render.py` | `tools/md2html/test_md2html.py` | `tools/md2html/README.md` | Also QG-009 for Markdown |
| DOCPUB-003 | Implemented | `tools/md2html/template.html` | `tools/md2html/test_md2html.py` | `tools/md2html/README.md` | |
| QG-001 | Implemented | `Makefile` (`lint`), `pyproject.toml` | CI `gates` job | `CLAUDE.md` | |
| QG-002 | Implemented | `Makefile` (`types`) | CI `gates` job | `CLAUDE.md` | |
| QG-004 | Implemented | `tools/check-loc/check_loc.py` | CI `gates` job | `docs/architecture/loc-exceptions.md` | Covers `src/` and `tools/`; test files are guidance only |
| QG-005 | Implemented | `pyproject.toml` (ruff C901 ≤ 10, PLR0915 ≤ 50 statements) | CI `gates` job | | |
| QG-006 | In progress | `Makefile` (`--cov-fail-under=90`) | CI `gates` job | | Per-module 95% branch targets come with critical logic in M2 |
| QG-007 | Implemented | `.github/workflows/ci.yml` (gitleaks) | CI `secrets` job | | |
| QG-003 | In progress | `pyproject.toml` (ruff D, Google convention) | CI `gates` job | | Section-presence checker (`tools/check-docstrings`) in M1 |
