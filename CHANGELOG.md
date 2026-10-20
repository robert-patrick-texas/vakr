# Changelog

All notable changes to vakr are recorded here. Versions follow Semantic Versioning, and the version itself lives only in [`VERSION`](VERSION). Until MVP acceptance, minor releases may contain breaking changes; each one is listed here along with the migration that handles it.

## Unreleased

## 0.1.0 - 2026-10-20

First pre-release. It delivers the pipeline only: the package, signed self-update, configuration upgrade, and the field data kit. Production hosts can install it, update themselves, and send field data back, but vakr does not monitor any devices yet. Monitoring arrives in milestones M2–M4.

### Added

- Blueprint revision 1.1: MVP definition, delivery and lifecycle tooling, portable tools, and default values.
- Owner decisions of 2026-10-20 recorded in the blueprint: install under `/opt/vakr`, enterprise proxy and TLS-inspection CA support, a strict X.509 policy with an opt-in fallback, `openssl cms` field bundles, and interface classification from description keywords.
- `vakr` CLI with `--version`, `doctor` (and `--network`), and `check health`, a Nagios-format self-check for CheckMK/Nagios (OPS-001).
- Configuration catalog with packaged defaults, operator overrides, versioned config migrations, and the commands `vakr config show-effective`, `validate`, `upgrade`, `new-options`, `defaults`, and `template` (CFGM-001..007).
- `vakr self-update`: verified and signed GitHub-release updates with backup, rollback, an optional daily timer, offline install, and `hold_version` (UPD-001..008).
- Outbound HTTPS through proxies with an extra trusted-CA directory, and `vakr tls trust-ca` / `list` / `remove` for enterprise TLS-inspection CAs (EGRESS-001..006).
- `.deb` package for Ubuntu 26.04 that installs under `/opt/vakr` with a bundled Python 3.15, built reproducibly in a container (`make deb`, `make package-test`; PKG-001..005).
- `tools/release`: signed releases (SHA256SUMS plus an SSH signature), with a dry run.
- `tools/fieldkit`: one-command production data collection into one encrypted bundle (CMS AES-256-GCM), plus the developer-side `field_inbox.py` (FIELD-001..010).
- `tools/check-updates` (`make updates`), a dependency vulnerability audit (`make audit`), and Dependabot (TOOLS-001..003, QG-008).
- `tools/check-docstrings` (QG-003), `tools/check-loc` (QG-004), and `tools/check-version` (VER-003) quality gates; CI with a gitleaks secret scan.
- Documentation site: a GitHub link and a left-side page index on every page (DOCPUB-004).
- `tools/md2html`: portable Markdown-to-HTML converter with a link checker, plus a GitHub Pages workflow.
- Apache-2.0 license, `VERSION`, and `CLAUDE.md` agent instructions.
