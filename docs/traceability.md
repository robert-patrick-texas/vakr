# Requirements traceability

This table maps each requirement to its code, tests, and documentation (blueprint section 1, Appendix B). Update it in the same change that implements or changes a requirement. A requirement not listed here is `Not started`.

Statuses: `Not started`, `In progress`, `Implemented`, `Verified`, `Deferred-approved`, `Not applicable-approved`.

## Versioning, packaging, and release

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| VER-001 | In progress | `VERSION`, `pyproject.toml` (hatch dynamic version), `src/vakr/version.py`, `packaging/build-deb.sh`, `tools/release/release.py`, `tools/fieldkit/build_pyz.py` | `tests/unit/test_cli.py::test_version_flag_prints_installed_version`, `packaging/container/install-test.sh` | Blueprint 28.2 | ADR-029. Not yet wired: Go `-ldflags` and documentation footers. |
| VER-002 | Implemented | `tools/release/release_version.py`, `src/vakr/lifecycle/update/versions.py` | `tools/release/test_release_version.py`, `tests/unit/lifecycle/test_update_versions.py` | `docs/development/release.md` | Results are cross-checked against `dpkg --compare-versions` and `packaging.version`. |
| VER-003 | Implemented | `tools/check-version/check_version.py` | `make version` in CI | Blueprint 28.2 | `src/vakr/defaults/options.toml` is allowlisted because its `since` values name releases. |
| VER-004 | In progress | `tools/release/release_publish.py` | `tools/release/test_release_publish.py` | `docs/development/release.md` | Release notes are taken from the matching `CHANGELOG.md` section. |
| PKG-001 | Implemented | `packaging/nfpm.yaml`, `packaging/build-deb.sh`, `packaging/container/build.sh`, `packaging/Dockerfile` | `make package-test`, `packaging/tests/test_package_spec.py`, CI `Package` job | `docs/development/release.md`, `packaging/README.md` | ADR-025. The spec is already RPM-ready. |
| PKG-002 | Implemented | `packaging/nfpm.yaml`, `packaging/container/build.sh` | `install-test.sh`, `test_package_spec.py::test_nfpm_places_only_integration_points_outside_opt` | `docs/operations/install.md` | `/usr/share/doc/vakr/{copyright,changelog.gz}` and the lintian overrides are required by Debian policy. |
| PKG-003 | Implemented | `packaging/sysusers.d/`, `packaging/tmpfiles.d/`, `packaging/scripts/deb/postinst` | `install-test.sh`, `systemd-test.sh`, `packaging/tests/test_units.py` | `docs/operations/install.md` | `vakr setup` gating arrives with the service units (M3/M4). |
| PKG-004 | Implemented | `packaging/Dockerfile`, `packaging/lintian/vakr`, `packaging/build-constraints.txt` | lintian `--fail-on error`, `PACKAGE_TEST_REPRO=1`, `test_package_spec.py` | `docs/development/release.md` | Rebuilds are byte-identical. Every lintian override carries a justification. |
| PKG-005 | Implemented | `packaging/scripts/deb/prerm`, `packaging/scripts/deb/postrm` | `install-test.sh`, `systemd-test.sh` | `docs/operations/install.md` | `vakr data purge` is not built yet. |
| PKG-006 | Not started | | | | Starts after MVP-ACC-10. |
| QG-010 | In progress | `.github/workflows/package.yml`, `packaging/package-test.sh` | CI `Package` job | `docs/development/release.md` | The `.deb` is reproducible; the sdist is not built yet. |

## Self-update and outbound HTTPS

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| UPD-001 | Implemented | `src/vakr/lifecycle/update/{github,releases,verify,cache,sources}.py`, `src/vakr/cli/cmd_selfupdate.py`, `packaging/release-signers`, `tools/release/release_artifacts.py` | `tests/unit/lifecycle/test_update_verify.py` (UPD-T02), `test_update_flow.py`, `tools/release/test_release_artifacts.py` | `docs/operations/updates.md` | ADR-026. Packages are verified in a root-owned staging area. The signers file is still a placeholder until the owner adds the release key. |
| UPD-002 | Implemented | `src/vakr/lifecycle/update/{engine,backup,process}.py` | `test_update_flow.py::test_upd_t01_update_to_next_release`, `install-test.sh` (N→N+1) | `docs/operations/updates.md` | |
| UPD-003 | Implemented | `packaging/systemd/vakr-update.{service,timer}`, `src/vakr/cli/cmd_selfupdate.py` | `packaging/tests/test_units.py`, `systemd-test.sh`, `test_selfupdate_cli.py::test_timer_invocation_updates_once` | `docs/operations/install.md`, `docs/operations/updates.md` | |
| UPD-004 | Implemented | `src/vakr/lifecycle/update/updater.py` | `test_update_verify.py::test_upd_t04_offline_file_install` | `docs/operations/updates.md` | |
| UPD-005 | Implemented | `src/vakr/lifecycle/update/{engine,policy}.py` | `test_update_flow.py::test_manual_rollback_restores_previous_version` | `docs/operations/updates.md` | The `manual-update-required` marker in release notes blocks timer-driven updates. |
| UPD-006 | Implemented | `src/vakr/lifecycle/update/{engine,state,policy}.py`, `src/vakr/health/lifecycle_checks.py` | `test_update_flow.py::test_upd_t03_health_failure_rolls_back_and_is_not_retried` | `docs/operations/updates.md` | |
| UPD-007 | Implemented | `src/vakr/lifecycle/update/{lock,journal}.py` | `test_update_flow.py`, `test_lifecycle_support.py::test_journal_native_protocol` | `docs/operations/updates.md` | |
| UPD-008 | Implemented | `src/vakr/lifecycle/update/{releases,policy}.py` | `test_update_flow.py::test_upd_t05_timer_updates_once_then_noops_and_respects_hold` | `docs/operations/updates.md` | |
| EGRESS-001 | Implemented | `src/vakr/lifecycle/egress/{proxy,transport,client}.py`, `src/vakr/defaults/options.toml` (`network.*`) | `test_egress_proxy.py` (EGRESS-T03), `test_egress_tls.py` | `docs/operations/updates.md` | ADR-032. Credentials in proxy URLs are refused. The credential provider exists now (`secret(name)`), so authenticated proxies can be added. |
| EGRESS-002 | Implemented | `src/vakr/lifecycle/egress/truststore.py` | `test_egress_tls.py` (EGRESS-T01) | `docs/operations/updates.md` | |
| EGRESS-003 | Implemented | `src/vakr/lifecycle/egress/certinfo.py` (`validate_ca`, `strict_profile_gaps`), `castore.py` (`install(strict_x509=)`), `src/vakr/cli/cmd_tls.py`, `src/vakr/cli/tls_strict.py` | `test_tls_cli.py` (EGRESS-T02), `test_egress_tls.py` (EGRESS-T01), `test_egress_strict.py` (EGRESS-T05, cross-checked with `openssl verify -x509_strict`) | `docs/operations/updates.md` | ADR-032. A CA with `keyUsage` but no `keyCertSign` is refused in both modes, because OpenSSL rejects it in every mode. |
| EGRESS-004 | Verified | `src/vakr/lifecycle/egress/peek.py` (the single allowlisted function) | `test_static_rules.py` (EGRESS-T04) | `docs/operations/updates.md` | |
| EGRESS-005 | Implemented | `src/vakr/lifecycle/egress/{diagnose,report,strict,peek}.py`, `vakr doctor --network`, `vakr self-update --check`, `tools/fieldkit/fieldkit_egress.py` | `test_egress_tls.py`, `test_selfupdate_cli.py`, `test_egress_strict.py`, `tools/fieldkit/test_fieldkit_egress.py` | `docs/operations/updates.md`, `docs/operations/field-data.md` | Strict-only failures are recognized by OpenSSL 3 verify codes and confirmed with a handshake-only relaxed retry. Real requests never fall back. |
| EGRESS-006 | Implemented | `src/vakr/lifecycle/egress/expiry.py`, `src/vakr/health/lifecycle_checks.py` | `test_tls_cli.py::test_egress_006_ca_expiry_health` | `docs/operations/updates.md` | |
| EGRESS-007 | Implemented | `src/vakr/defaults/options.toml` (`network.tls_strict_x509`), `src/vakr/lifecycle/egress/truststore.py` (`build_context(strict_x509=)`), `client.py`, `src/vakr/health/lifecycle_checks.py` (`check_tls_policy`), field kit `--no-strict-x509` | `test_egress_strict.py` (EGRESS-T05), `tests/unit/config/test_render.py`, `tools/fieldkit/test_fieldkit_egress.py` | `docs/operations/configuration.md`, `docs/operations/updates.md`, `docs/operations/field-data.md` | ADR-032. Only `VERIFY_X509_STRICT` is cleared. The strict flag is set explicitly, because `ssl.SSLContext()` never set it. |

## Configuration

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| CFGM-001 | Implemented | `src/vakr/defaults/options.toml`, `src/vakr/lifecycle/configmerge/{layering,render}.py`, `vakr config defaults` | `tests/unit/config/test_upgrade.py::test_new_release_options_apply_defaults_without_edits` (CFGM-T01), `test_render.py` | `docs/operations/configuration.md` (generated) | ADR-027 |
| CFGM-002 | Implemented | `src/vakr/lifecycle/configmerge/{migrations,operations,transforms,tree,upgrade,fileio}.py` | `test_upgrade.py` (CFGM-T02, CFGM-T05), `test_operations.py` | `docs/operations/configuration.md` | ADR-027, ADR-031 |
| CFGM-003 | Implemented | `src/vakr/lifecycle/configmerge/{upgrade,fileio}.py` | `test_upgrade.py::test_failed_migration_or_validation_leaves_files_byte_identical` (CFGM-T03) | `docs/operations/configuration.md` | |
| CFGM-004 | Implemented | `src/vakr/lifecycle/configmerge/{annotate,state}.py`, `vakr config new-options` | `test_new_options.py` (CFGM-T04) | `docs/operations/configuration.md` | |
| CFGM-005 | Implemented | `packaging/scripts/deb/postinst`, `vakr config template` | `install-test.sh`, `test_render.py::test_template_is_minimal_valid_and_versioned` | `docs/operations/install.md` | No dpkg conffiles are used. |
| CFGM-006 | Implemented | `src/vakr/lifecycle/configmerge/{layering,lines,migrations}.py` | `test_layering.py::test_unknown_keys_suggest_the_closest_valid_key_with_file_and_line` | `docs/operations/configuration.md` | `strict=False` loading lets recovery commands (`self-update`, `tls`) run on a config they can't fully validate. |
| CFGM-007 | In progress | `src/vakr/lifecycle/configmerge/operations.py` | `test_operations.py` | | Inventory, policy, and dashboard files are not loaded yet. |
| CONF-006 | In progress | `src/vakr/lifecycle/configmerge/layering.py`, `vakr config show-effective --sources` | `test_layering.py::test_precedence_defaults_main_dropins_overrides_with_sources` | `docs/operations/configuration.md` | Environment and CLI overrides are not wired yet. |
| CONF-009 | In progress | `src/vakr/lifecycle/configmerge/checks.py`, `vakr config validate` | `test_layering.py` | `docs/operations/configuration.md` | Reference, permission, and database checks come later. |

## Credentials

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| SEC-001 | In progress | `src/vakr/credentials/vocabulary.py` (`CredentialReference`) | `tests/unit/credentials/test_model.py`, `test_provider.py` | `docs/operations/credentials.md` | Device records keep a reference or name only. Reference-integrity checking (CFG-004) is not wired yet. |
| SEC-002 | Implemented | `src/vakr/credentials/{port,provider,protected,policy,parse,entry}.py` | `test_provider.py`, `test_permissions.py` (CRED-001) | `docs/operations/credentials.md` | The `CredentialResolver` port leaves room for a later Vault provider. |
| SEC-003 | In progress | `src/vakr/credentials/{secret,errors,parse,redact}.py`, `src/vakr/cli/cmd_credentials.py` | `test_redaction_corpus.py` (CRED-002), `test_secret.py` | `docs/operations/credentials.md` | API, diagnostic, and audit payloads must reuse `Secret` and `redact_text`, and extend CRED-002. |
| SEC-016 | Implemented | `src/vakr/credentials/{policy,protected,health}.py` | `test_permissions.py` (CRED-001), `test_credentials_cli.py` | `docs/operations/credentials.md` | Strict by default. Development mode is available only through a constructor flag or `--dev`. |
| SEC-020 | In progress | `src/vakr/credentials/redact.py` | `test_redaction_corpus.py` (CRED-002) | `docs/operations/credentials.md` | Redaction helpers are ready; the support bundle comes later. |
| CONF-007 | Implemented | `src/vakr/credentials/provider.py` | `test_provider.py` | `docs/operations/credentials.md` | |
| SNMP-002 | Implemented | `src/vakr/credentials/{model,vocabulary,entry}.py` | `test_model.py`, `test_provider.py` | `docs/operations/credentials.md` | v3 passphrases need at least 8 characters (RFC 3414). |
| SNMP-003 | In progress | `src/vakr/credentials/model.py` (`is_recommended`) | `test_model.py`, `test_credentials_cli.py` | `docs/operations/credentials.md` | Security posture is reported, not enforced. SNMPv3 polling comes after MVP. |

## Measurement logic

Code lives in `src/vakr/logic/` and tests in `tests/unit/logic/`. Rules and the interpretations behind them are in `docs/architecture/quality-states.md` and blueprint Appendix E.17. `vakr.logic` has 100% statement and branch coverage.

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| ICMP-003/004/008 | In progress | `icmp.py` (`ProbeGroup`, `ProbeGroup.from_raw`, `summarise_probes`), `stats.py` | `test_logic_icmp.py` (ICMP-T01..T04) | `quality-states.md` | Storage of these results is still to come. |
| ICMP-005/006/007/010 | Implemented | `icmp.py` (`snmp_plan`), `stats.py` (`pstdev_half_even`) | ICMP-T01..T04, `test_snmp_plan_per_strategy`, `test_pstdev_matches_decimal_reference` | `quality-states.md` | The collector must call `snmp_plan`. |
| ICMP-013/014 | In progress | `icmp.py`, `status_table.py` | `test_snmp_plan_per_strategy`, `test_strategy_skips` | `quality-states.md` | E.17 #6. ICMP-T05 integration is pending. |
| UPTIME-002/004/005 | Implemented | `uptime.py` | `test_logic_uptime.py` (UP-T01..T03, property tests) | `quality-states.md` | E.17 #3 |
| UPTIME-003 | In progress | `uptime.py` (evidence text) | `test_reset_evidence_names_the_agent_not_the_chassis` | `quality-states.md` | UI wording is pending. |
| RATE-001/003/004/005/006/007/009 | Implemented | `baseline.py`, `rates.py`, `ranking_errors.py` | `test_logic_rates.py` (RATE-T01..T07, T09), `test_logic_rates_property.py` | `quality-states.md` | E.17 #8, #10, #11 |
| RATE-002 | In progress | `rates.py` (`RateResult`) | RATE-T01 | `quality-states.md` | Storage is pending. |
| RATE-008/010 | In progress | `capacity.py` | `test_logic_capacity.py` (RATE-T08) | `quality-states.md` | E.17 #9 (precise `ifSpeed` below 20 Mb/s). The UI label is pending. |
| TIME-005/006 | In progress | `freshness.py` | `test_logic_freshness.py` | `quality-states.md` | Config keys arrive in M3. |
| LIFE-001/003/004/006 | In progress | `lifecycle_model.py`, `lifecycle.py` | `test_logic_lifecycle.py` (LIFE-T01) | `quality-states.md` | E.17 #12. The scheduler and persistence still need to use it. |
| MPOS-007 | In progress | `status.py` (`composite_status`) | `test_logic_status.py::test_composite` | `quality-states.md` | E.17 #7. The UI is pending. |
| OBS-003 | In progress | `icmp.py`, `status_table.py` (row 3) | `test_collector_context`, `test_e2_rows` | `quality-states.md` | |
| UI-005 | In progress | `status.py` (`device_sort_key`) | `test_device_list_order` | `quality-states.md` | WEB-T01 covers the logic only. |
| DASH-003/004/005 | In progress | `ranking.py`, `ranking_errors.py`, `stats.py` | `test_logic_ranking.py` (DASH-T01..T03) | `quality-states.md` | E.17 #13. The query and UI are pending. |
| RET-004 | In progress | `stats.py` (`p95`) | `test_logic_stats.py::test_nearest_rank` | `quality-states.md` | Rollups must import `p95` from here. |
| QG-006 | In progress | `vakr.logic` | `--cov-branch` 100% | | Not yet enforced as a CI gate. |

## Collection policy and classification

Code lives in `src/vakr/policy/` and tests in `tests/unit/policy/`. `vakr.policy` has 100% line and branch coverage. The operator guide is `docs/operations/policies.md`; limits and rules are in Appendix E.18.

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| POLICY-001 | Implemented | `rules.py`, `rules_parse.py`, `engine.py` | `test_engine.py`, `test_rules.py` | `policies.md` | The §11.3 `exclude_<field>_patterns` shorthand is accepted. |
| POLICY-002 | Implemented | `rules.py`, `engine.py`, `resolve.py` | `test_engine.py::test_tier_outranks_priority_and_file_order` | `policies.md` | E.18 |
| POLICY-003 | Implemented | `facts.py`, `facts_parse.py` | `test_engine.py::test_every_match_field_reads_its_fact` | `policies.md` | Facts come from a file until the M3 inventory exists. |
| POLICY-004 | In progress | `engine.py`, `trace.py`, `render.py`, `src/vakr/cli/cmd_policy.py` | `test_explain_cli.py` (CFG-009) | `policies.md` | The CLI is done; UI and API explain are pending. |
| POLICY-005 | Implemented | `patterns.py`, `regex_safety.py`, `charsets.py`, `src/vakr/config/commands.py` (validate) | `test_patterns.py`, `test_regex_safety.py`, `test_charsets.py` (CFG-005), `tests/unit/config/test_validate_keywords.py` | `policies.md` | E.18. Uses the private `re._parser` module. |
| POLICY-006 | Implemented | `engine.py` | `test_engine.py::test_evaluation_is_deterministic` | `policies.md` | |
| POLICY-007 | In progress | `settings.py` | `test_rules.py::test_settings_outside_documented_limits_fail` | `policies.md` | Limits are in E.18; the scheduler applies them in M3. |
| META-011 | In progress | `resolve.py`, `settings.py` | `test_engine.py::test_meta_012_annotation_then_keyword_then_rule_then_default` | `policies.md` | Storage is pending. |
| META-012 | In progress | `keywords.py`, `classify.py`, `resolve.py` | `test_classify.py`, `test_engine.py` | `policies.md` | ADR-033. Description-tag storage and DISC-011 refresh are pending. |
| META-013 | In progress | `classify.py` (`not_yet_classified`) | `test_classify.py` | `policies.md` | The UI filter and `vakr interface tag` are pending. |
| DISC-007 | In progress | `trace.py` (`Decision.inventoried`) | `test_engine.py::test_suppressed_interfaces_stay_inventoried` | `policies.md` | Discovery itself is M3. |
| LIFE-007 | In progress | `ordering.py` | `test_ordering.py` | `policies.md` | The device page and TUI are pending. |
| SEC-019 | In progress | `patterns.py`, `regex_safety.py` | `test_patterns.py`, `test_regex_safety.py` | `policies.md` | Covers patterns only. |

## Domain model, ports, and envelope

Code lives in `src/vakr/domain/`, `src/vakr/ports/`, and `src/vakr/adapters/memory/`. The contract suites are in `tests/contract/` and the golden fixtures in `tests/fixtures/observations/`. See `docs/architecture/domain.md` and `docs/architecture/storage.md`, and Appendix E.19.

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| ARCH-001 | Implemented | `src/vakr/domain/`, `src/vakr/ports/` | `tests/unit/domain/test_domain_boundaries.py` | `domain.md`, `storage.md` | Imports only the standard library, `vakr.domain`, and `vakr.ports`. |
| ARCH-002 | Implemented | `domain/{envelope,codec,codec_fields,codec_values,json_values,timestamps}.py` | `test_domain_envelope.py`, golden fixtures | `domain.md` | Schema v1. Timestamps are integer µs. The checksum covers the whole envelope (E.19). |
| META-001/002 | Implemented | `domain/{ids,inventory,interfaces}.py` | `test_domain_records.py`, DB-001 suite | `domain.md` | IDs are UUIDv7; `if_index` is an observed attribute. |
| META-003..007 | Implemented | `domain/{inventory,interfaces,catalog}.py`, `ports/metadata/filters.py` | DB-001 suite, `test_domain_records.py` | `domain.md`, `storage.md` | Interface field `is_wan_circuit`; config key `wan_circuit`. |
| META-008 | Implemented | `domain/ownership.py`, repository `update(writer=)` | `contract_metadata_rules.py::test_meta008_*` | `storage.md` | Ownership is checked before version (E.19). |
| META-009 | Implemented | `set_admin_state`, `set_lifecycle`; the ports have no delete | `test_meta009_retiring_keeps_records_and_history` | `storage.md` | |
| META-011/013 | Implemented | `InterfaceAnnotation.interest`, `Interface.tags` vs `description_tags` | DB-001, `test_meta008_interface_write_paths` | `domain.md` | |
| IFID-005 | Implemented | `domain/control.py` (`IdentityHistoryEntry`), `InterfaceRepository` history methods | `test_ifid005_identity_history` | `domain.md` | |
| SEC-018 | In progress | `AuditEvent`, `AuditRepository` | `test_sec018_audit_events` | `storage.md` | The application layer emits events. |
| STORE-008/009/012 | Implemented | `domain/{observations,interface_observations,series,series_assembly}.py`, `ObservationWriter` | codec completeness, `test_store009_*`, `test_store012_*`, `test_db006_*` | `domain.md`, `storage.md` | E.19 adds the native-gap rule for M4. |
| STORE-010/013 | In progress | `ObservationWriter` contract, memory adapter | `test_store010_*`, `TelemetryWriteContract` | `storage.md` | Needs the SQLite parity run. |
| STATE-001/002 | In progress | `CurrentStateQuery`, `domain/projections.py` | `TelemetryStateContract` | `storage.md` | |
| STATE-003/004/005 | Implemented | state read models, collector-scoped series, `resolution_spans` | DB-005, `test_state004_*`, `test_db006_*` | `domain.md`, `storage.md` | |
| API-002/005 | Implemented | UUID-only read models, `domain/paging.py` keyset cursors | DB-007, `test_db001_paging_*`, `test_api005_*` | `storage.md` | Enforced at the port level; the HTTP API comes in M4. |
| API-006 | In progress | `TimeWindow`, `max_points`, `Series.truncated` / `resolutions` | `test_api006_point_cap_and_metric_family` | `domain.md` | Timezone rendering comes in M4. |
| TIME-001/005/006 | Implemented | `domain/timestamps.py`, state read models | `test_timestamps_are_utc_and_exact`, DB-005, `test_e3_freshness_threshold` | `domain.md` | |
| ICMP-004/005 | In progress | The domain uses `None`; `-1` is a storage detail | golden fixtures, `test_icmp_series_metrics` | `storage.md` | The SQLite adapter will store `-1`. |
| RATE-004, LIFE-002 | Implemented | `InterfaceObservation` baseline check, `set_lifecycle` | `test_interface_sample_invariants`, `test_meta009_*` | `domain.md` | |
| RET-007 | In progress | `RetentionManager` | `test_retention_follows_policy` | `storage.md` | Audited purge comes in M4. |
| DB-001..007 | In progress | `tests/contract/` suites | The memory adapter passes all of them | `storage.md` | SQLite metadata and telemetry runs are next. |

## Field data

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| FIELD-001 | Implemented | `tools/fieldkit/fieldkit.py`, `tools/fieldkit/build_pyz.py`, `src/vakr/cli/cmd_field.py` | `tools/fieldkit/test_build_pyz.py`, `tests/unit/test_cmd_field.py` | `docs/operations/field-data.md` | ADR-028, ADR-030 |
| FIELD-002 | Implemented | `tools/fieldkit/fieldkit_{host,snmp,paths,app}.py` | `test_fieldkit_snmp.py`, `test_fieldkit_cli.py` | `docs/operations/field-data.md` | |
| FIELD-003 | Implemented | `tools/fieldkit/fieldkit_{bundle,collect}.py` | `test_fieldkit_bundle.py`, `test_field_inbox.py::test_checksum_mismatch_is_rejected` | `docs/operations/field-data.md` | Bundles are deterministic tar.gz files with size caps. |
| FIELD-004 | Implemented | `tools/fieldkit/fieldkit_snmpconf.py`, `fieldkit_bundle.py` | `test_fieldkit_snmp.py::test_community_never_in_argv_environment_or_bundle` | `docs/operations/field-data.md` | The community is passed through a private `SNMPCONFPATH` and checked against net-snmp 5.9.4. |
| FIELD-005 | Implemented | `tools/fieldkit/fieldkit_{pseudo,scrub,mibscrub}.py` | `test_fieldkit_pseudo.py` | `docs/operations/field-data.md` | Classification keywords are kept during pseudonymization. |
| FIELD-006 | Implemented | `tools/fieldkit/fieldkit_crypto.py` | `test_fieldkit_crypto.py` | `docs/operations/field-data.md` | CMS AuthEnvelopedData with AES-256-GCM and RSA-OAEP. |
| FIELD-007 | Implemented | `tools/fieldkit/field_inbox.py` (`fetch`) | `test_field_inbox.py::test_fetch_downloads_over_verified_https` | `docs/operations/field-data.md` | |
| FIELD-008 | Implemented | `tools/fieldkit/field_inbox*.py` | `test_field_inbox.py` | `docs/operations/field-data.md` | Extraction rejects unsafe archive members. |
| FIELD-009 | Implemented | `tools/fieldkit/fieldkit_{collect,run}.py` | `test_fieldkit_cli.py`, `test_fieldkit_snmp.py::test_failed_walks_and_time_budget_are_recorded` | `docs/operations/field-data.md` | |
| FIELD-010 | Implemented | `tools/fieldkit/fieldkit_{egress,tunnel,der}.py` | `test_fieldkit_egress.py` | `docs/operations/field-data.md` | |

## Operations, tooling, and quality gates

| Requirement | Status | Code/module | Tests | Documentation | ADR/notes |
|---|---|---|---|---|---|
| OPS-001 | In progress | `src/vakr/health/`, `src/vakr/cli/cmd_check.py`, `src/vakr/config/health.py` | `tests/unit/test_cli.py`, `test_selfupdate_cli.py::test_check_update_health`, `tests/unit/config/test_cli_config.py::test_check_config_statuses` | Blueprint 8.22 | Current checks: version, python, config, update, trusted CAs, TLS policy. The list grows with each subsystem. |
| SVC-007 | In progress | `src/vakr/cli/cmd_doctor.py` (`--network`) | `tests/unit/test_cli.py` | Blueprint 8.22 | |
| TOOLS-001 | Implemented | `tools/check-updates/`, `make updates` | `tools/check-updates/test_*.py` | `tools/check-updates/README.md`, `docs/development/updates.md` | |
| TOOLS-002 | Implemented | `docs/development/updates.md`, `.github/dependabot.yml` | Process step | `docs/development/updates.md` | Deferred updates are recorded in `CHANGELOG.md`. |
| TOOLS-003 | In progress | `tools/check-updates/updates_checks.py` | `test_check_updates.py` | `docs/development/updates.md` | |
| DOCPUB-001 | Implemented | `tools/md2html/`, `.github/workflows/pages.yml` | `tools/md2html/test_md2html.py` | `tools/md2html/README.md` | |
| DOCPUB-002 | Implemented | `tools/md2html/md2html_render.py` | `tools/md2html/test_md2html.py` | `tools/md2html/README.md` | Also covers QG-009 for Markdown. |
| DOCPUB-003 | Implemented | `tools/md2html/template.html` | `test_md2html.py`, `test_md2html_nav.py` | `tools/md2html/README.md` | |
| DOCPUB-004 | Implemented | `tools/md2html/md2html_nav.py`, `template.html` | `tools/md2html/test_md2html_nav.py` | `tools/md2html/README.md` | |
| QG-001 | Implemented | `Makefile` (`lint`), `pyproject.toml` | CI `gates` job | `CLAUDE.md` | |
| QG-002 | Implemented | `Makefile` (`types`) | CI `gates` job | `CLAUDE.md` | |
| QG-003 | Implemented | `tools/check-docstrings/`, ruff D rules | `tools/check-docstrings/test_*.py`, CI `gates` job | `tools/check-docstrings/README.md` | `>>>` examples are syntax-checked. |
| QG-004 | Implemented | `tools/check-loc/check_loc.py` | CI `gates` job | `docs/architecture/loc-exceptions.md` | Covers `src/` and `tools/`; test files are guidance only. |
| QG-005 | Implemented | `pyproject.toml` (ruff C901 ≤ 10, PLR0915 ≤ 50) | CI `gates` job | | |
| QG-006 | In progress | `Makefile` (`--cov-fail-under=90`) | CI `gates` job | | Per-module 95% branch-coverage targets come with M2 logic. |
| QG-007 | Implemented | `.github/workflows/ci.yml` (gitleaks) | CI `secrets` job | | |
| QG-008 | In progress | `Makefile` (`audit`), CI `audit` job | CI `audit` job | `docs/development/updates.md` | Vulnerability scan only; license and SBOM checks are still to come. |
