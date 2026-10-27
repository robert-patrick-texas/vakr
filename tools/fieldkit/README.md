# fieldkit

A portable, standard-library-only kit that gets production facts back to developers who can't log in to production. It has two halves:

- **`vakr-fieldkit`** (`fieldkit.py` and the `fieldkit_*.py` modules) runs on the production host. One command collects the chosen profiles into one `tar.gz` with a checksummed manifest, optionally pseudonymizes it, and encrypts it to the development host's certificate with `openssl cms` (AES-256-GCM AuthEnvelopedData, RSA-OAEP/SHA-256).
- **`field_inbox.py`** runs on the development host. It fetches a bundle over HTTPS or picks up `scp` drop-offs, then decrypts, verifies, and safely extracts each one and prints a summary.

The operator guide is [docs/operations/field-data.md](../../docs/operations/field-data.md). The requirements are in [blueprint 28.5](../../tool-blueprint.md#285-field-data-from-production), and the portability rules this tool follows are in [section 29](../../tool-blueprint.md#29-portable-scaffolding-tools).

## Files

| File | Role |
|---|---|
| `fieldkit.toml` | Every project-specific value (names, version file, recipient, keywords, reachability URLs, application profile, inbox paths) |
| `fieldkit.py` | Field kit entry point and command line |
| `fieldkit_plan.py`, `fieldkit_collect.py` | Preflight, profile run, manifest, encryption, final line |
| `fieldkit_host.py`, `fieldkit_snmp.py`, `fieldkit_paths.py`, `fieldkit_app.py` | The `host`, `snmp`, `paths`, and application profiles |
| `fieldkit_snmpconf.py` | Community handling: protected file or prompt, private `snmp.conf`, device and OID validation |
| `fieldkit_mrtg.py`, `fieldkit_mrtgcfg.py`, `fieldkit_mrtgmodel.py`, `fieldkit_mrtgtarget.py` | The `mrtg` profile: MRTG configuration, `Target[]` forms, and MRTG's `.log` or RRD data |
| `fieldkit_smokeping.py`, `fieldkit_smokecfg.py`, `fieldkit_smokemodel.py` | The `smokeping` profile: the Targets tree, probes, and RRD data |
| `fieldkit_acceptance.py`, `fieldkit_compare.py`, `fieldkit_rrd.py`, `fieldkit_hosts.py` | The `acceptance` profile (the application's comparison export), the parallel-run preflight, rrdtool reads, and host aliases |
| `fieldkit_egress.py`, `fieldkit_tunnel.py`, `fieldkit_der.py` | Step-by-step HTTPS reachability test through proxies, and certificate name decoding |
| `fieldkit_pseudo.py`, `fieldkit_scrub.py`, `fieldkit_mibscrub.py`, `fieldkit_tablescrub.py` | Keyed pseudonyms, and scrubbing of text, SNMP walks, and CSV tables column by column |
| `fieldkit_bundle.py`, `fieldkit_context.py`, `fieldkit_run.py`, `fieldkit_crypto.py` | Bundle writer, run context, bounded commands, OpenSSL CMS |
| `build_pyz.py` | Builds the reproducible zipapp |
| `field_inbox.py`, `field_inbox_setup.py`, `field_inbox_extract.py`, `field_inbox_summary.py` | Development-host inbox |
| `recipients/vakr-dev.pem` | Public recipient certificate. The private key never enters the repository. |
| `test_*.py`, `conftest.py`, `testing_servers.py` | Tests, a throwaway PKI, and loopback TLS and proxy servers |
| `testdata/compare/` | A cfgmaker-style MRTG configuration, a Debian SmokePing `config.d` layout, and recorded MRTG `.log` and `rrdtool` output: the redaction corpus of the parallel-run profiles (every credential in it is a fake `fk-test-...` value) |

## Build

```sh
uv run tools/fieldkit/build_pyz.py --output dist/vakr-fieldkit.pyz
```

The zipapp contains the `fieldkit*.py` modules, a generated `__main__.py` that refuses Pythons older than the configured version, and a generated `fieldkit_embedded.py`. That generated module holds the `[fieldkit]` values, the version read from the project's `VERSION` file, and the recipient certificate. Entries have fixed timestamps, permissions, and order, so building twice from the same sources gives identical bytes. Packaging installs the result at `/opt/vakr/share/fieldkit/vakr-fieldkit.pyz`.

## Test

```sh
uv run pytest tools/fieldkit
```

The tests generate throwaway keys with `openssl`, put stand-ins for `snmpbulkwalk`, `ping`, `rrdtool`, `vakr`, and `journalctl` on `PATH`, and use loopback servers. They prove:
- the community is never in argv, the environment, or the bundle;
- extraction rejects absolute paths, `..`, links, and devices;
- a tampered bundle fails to decrypt;
- pseudonyms are stable and keep the keywords;
- no MRTG community, SNMPv3 credential, SmokePing probe password, or URL credential from the redaction corpus reaches a bundle, and pseudonymized MRTG and application tables still join.

## Reuse in another project

Copy the directory and edit `fieldkit.toml`:
- the tool and bundle names;
- `version_file`;
- the recipient certificate, after running `field_inbox.py init-recipient` with your own `[inbox]` paths;
- the keywords;
- the reachability URLs;
- the `[fieldkit.app]` commands and journald units;
- the `[fieldkit.acceptance]` command that writes the application's comparison export.

No Python changes are needed. The modules import nothing from vakr.

## Safety notes

- The field kit only reads. It writes the bundle into `--output-dir` and, with `--pseudonymize`, a key at `~/.config/<tool_name>/pseudonym.key`. net-snmp's state directory is redirected into the private temporary directory as well.
- External commands are always started from argument lists, never through a shell.
- `peek_presented_certificate_unverified` in `fieldkit_egress.py` is the only TLS handshake without verification. It runs only after a verification failure, it reads the presented certificate's issuer, and it closes without sending data.
- `--no-strict-x509` clears only OpenSSL's strict X.509 profile flag; certificate and host-name verification stay on. After a strict-profile failure, `_strict_profile` makes one confirmation handshake with the same relaxation. It also closes without sending data, and it only changes the wording of the report.

## License

Apache License 2.0, like the rest of the repository. See [LICENSE](../../LICENSE).
