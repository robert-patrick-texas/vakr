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
| EGRESS-003 | Implemented | `src/vakr/lifecycle/egress/certinfo.py` (`validate_ca`, `strict_profile_gaps`, `is_self_signed`), `src/vakr/lifecycle/egress/castore.py` (`install(strict_x509=)`), `src/vakr/cli/cmd_tls.py` (`run_trust`), `src/vakr/cli/tls_strict.py` | `tests/unit/lifecycle/test_tls_cli.py` (EGRESS-T02), `tests/unit/lifecycle/test_egress_tls.py` (EGRESS-T01), `tests/unit/lifecycle/test_egress_strict.py` (EGRESS-T05, `openssl verify -x509_strict` cross-check) | `docs/operations/updates.md` (Trusting the CA; The strict X.509 profile) | ADR-032. A `keyUsage` without `keyCertSign` is refused in both modes because OpenSSL rejects it in every mode. `openssl verify -x509_strict -CAfile ca.pem ca.pem` skips strict rules on a one-certificate chain, so the leaf-chain form is the reference |
| EGRESS-005 | Implemented | `src/vakr/lifecycle/egress/diagnose.py`, `report.py`, `strict.py` (`assess`, `confirm_relaxed`), `peek.py`; `src/vakr/cli/cmd_tls.py` (`network_checks`); `src/vakr/cli/cmd_selfupdate.py` (`_print_diagnosis`); `tools/fieldkit/fieldkit_egress.py` | `tests/unit/lifecycle/test_egress_tls.py`, `test_selfupdate_cli.py`, `test_egress_strict.py`, `test_static_rules.py`; `tools/fieldkit/test_fieldkit_egress.py` | `docs/operations/updates.md` (Symptoms and diagnosis; The strict X.509 profile), `docs/operations/field-data.md` | ADR-032. Strict-only failures are recognized by OpenSSL 3 verify codes 78 and 80-94 (or their messages) and confirmed by a handshake-only retry with only the strict profile relaxed; real requests never fall back |
| EGRESS-007 | Implemented | `src/vakr/defaults/options.toml` (`network.tls_strict_x509`), `src/vakr/lifecycle/egress/truststore.py` (`build_context(strict_x509=)`), `client.py` (`EgressSettings.strict_x509`), `src/vakr/cli/cmd_tls.py` (`build_egress_settings`, `network_checks`), `src/vakr/cli/cmd_selfupdate.py`, `src/vakr/health/lifecycle_checks.py` (`check_tls_policy`), `src/vakr/health/registry.py`; `tools/fieldkit/fieldkit_egress.py` (`--no-strict-x509`) | `tests/unit/lifecycle/test_egress_strict.py` (EGRESS-T05), `tests/unit/config/test_render.py`; `tools/fieldkit/test_fieldkit_egress.py` | `docs/operations/configuration.md`, `docs/operations/updates.md`, `docs/operations/field-data.md`, `tools/fieldkit/README.md` | ADR-032. Clears only `VERIFY_X509_STRICT`. The strict flag is now set explicitly, because `ssl.SSLContext()` (unlike `create_default_context()`) never set it |
