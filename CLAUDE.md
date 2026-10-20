# vakr — instructions for Claude and other agents

`tool-blueprint.md` is authoritative. Read only the sections relevant to the task; the whole file is too large to load every time.

## Where we are

- The current goal is the MVP (blueprint §27). Follow the milestone order in §27.8, not the phase order in §20 (ADR-022).
- MVP scope means the Compact profile (SQLite/SQLite), SNMPv2c, Cisco IOS XE, and 100 devices / ~1,600 interfaces. Anything in §27.4's "Deferred" list is out of scope unless the owner says otherwise.
- Default values come from Appendix E. Do not invent thresholds; if one is missing, add it to Appendix E in the same change.
- Breaking changes are allowed before enterprise readiness (ADR-031). Config migrations (§28.4) and data migrations are still required.

## Non-negotiables (from blueprint §4.3, §17, and §25)

- Missing data is never zero. Collector or storage trouble is never reported as target-down. `ifIndex` is never identity.
- No SQL outside adapter modules. No database-driver types outside adapters. No blocking calls on the event loop.
- Every queue, retry, query, export, and concurrency limit is bounded.
- Secrets never appear in logs, exceptions, reprs, fixtures, bundles, or test data.
- Files under `src/` stay at or below 300 lines and functions at or below 50 lines.
- Every module needs a Google-style docstring: purpose, network context, dependencies, safety, and an example.
- Every function needs a Google-style docstring with Args, Returns, and Raises sections. Write "None" for an empty section.
- Comments explain *why*, not *what*.
- Cite requirement and test IDs (for example ICMP-004, RATE-T03) in tests, commits, and work reports. Update `docs/traceability.md` in the same change.
- Never silently resolve a blueprint contradiction. Record it as an ADR or issue, state the choice, and keep going.

## Project mechanics

- **Version:** the root `VERSION` file is the only place the version appears (VER-001). Never hard-code it anywhere else.
- **Toolchain:** use the latest stable Python (3.15), Go (1.27), uv, ruff, and ty. Run Python through `uv`.
- **Portable tools** under `tools/` and `src/vakr/lifecycle/` import nothing from vakr and take project values from arguments or config (§29).
- **Documentation:** every `.md` file is published to GitHub Pages by `tools/md2html`. Keep internal links valid; CI runs `--check`.
- **Never commit** `_site/`, `inbox/`, field bundles, or anything resembling credentials.
- **Production** runs at a separate site. When production data is needed, extend the field kit (§28.5) instead of asking the operator for manual steps.

## Agent work report (blueprint §20.1)

When finishing a task, report:

- the requirement IDs addressed;
- the files changed;
- the tests added and their exact results;
- the documentation and traceability updates made;
- any known limitations;
- confirmation that no `src/` file exceeds 300 lines and that no secrets were added.
