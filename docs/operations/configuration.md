# Configuration reference

*Generated from the option catalog (`src/vakr/defaults/options.toml`) by `vakr config defaults --markdown`. Do not edit this page by hand; a test fails when it is out of date.*

vakr reads the packaged defaults, then `/etc/vakr/config.toml`, then every `/etc/vakr/conf.d/*.toml` drop-in in name order; a later file overrides an earlier one key by key (CONF-006). Operator files hold only the settings you change (CFGM-001), so an option added by a new release takes its default below without any edit. Description keywords are the exception to replacement: entries from every file extend the packaged list.

Each operator file carries `config_version`. After a package upgrade, `vakr config upgrade` (run by `vakr self-update`) applies the release's config migrations, keeps your comments and formatting, writes a timestamped backup under `/var/lib/vakr/config-backups/`, and prints a diff; `--dry-run` only shows the diff (CFGM-002, CFGM-003). An unknown key is a validation error that names the file, line, and key, and suggests the closest valid key or the migration that removed it (CFGM-006).

Useful commands: `vakr config show-effective --sources` prints every effective value and the file and line that set it; `vakr config validate` checks files without starting collectors; `vakr config new-options [--annotate]` lists options added since the previous deployed release and can append them as comments to your files (CFGM-004); `vakr config template` prints the starter `config.toml`.

*Reload* `live` options apply on a configuration reload; `restart` options need a service restart. *Added in* names the release tag that introduced the option.

## Options

### `[profile]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `profile.name` | enum: lite \| compact \| hybrid-small \| standard \| enterprise | `"compact"` | restart | v0.1.0 | Deployment profile, which selects storage backends and sizing guidance. The MVP supports compact: SQLite metadata and telemetry for about 100 devices. |

### `[node]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `node.role` | enum: primary \| peer | `"primary"` | restart | v0.1.0 | Role of this node. A primary owns inventory and policy metadata; a peer pulls metadata from node.primary_url and keeps collecting while isolated (MPOS-003). |
| `node.position` | str | `""` | restart | v0.1.0 | Label for this node's network vantage point, shown in the UI, for example east. vakr setup sets it on first install. |
| `node.primary_url` | str (url) | `""` | restart | v0.1.0 | HTTPS URL of the primary node, for example https://vakr-east.example.net. Required when node.role is peer; ignored on a primary. |

### `[time]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `time.timezone` | str (timezone) | `""` | restart | v0.1.0 | IANA timezone for displayed times and day boundaries, for example America/New_York. Empty means the host timezone. |

### `[update]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `update.repository` | str | `"robert-patrick-texas/vakr"` | live | v0.1.0 | GitHub repository (owner/name) that vakr self-update checks for releases (UPD-001). |
| `update.channel` | enum: stable \| prerelease | `"prerelease"` | live | v0.1.0 | Release channel for self-update. MVP iterations are pre-releases, so prerelease is the default until MVP acceptance. |
| `update.hold_version` | str | `""` | live | v0.1.0 | Pin the deployment to this release; timer-driven updates will not go past it (UPD-008). Empty means no hold. |
| `update.health_settle_seconds` | int, >= 0, <= 600 | `30` | live | v0.1.1 | After installing an update, keep re-running the health check for up to this many seconds while it reports CRITICAL, so services that are still starting are not mistaken for a failed update and rolled back (UPD-006). 0 checks once. |

### `[network]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `network.https_proxy` | str (url) | `""` | restart | v0.1.0 | Proxy for all outbound HTTPS, such as release checks and peer replication (EGRESS-001). Empty falls back to the HTTPS_PROXY environment variable. Never put credentials here; proxy credentials come from the credential provider. |
| `network.no_proxy` | list[str] | `[]` | restart | v0.1.0 | Hosts and domains that bypass network.https_proxy. Empty falls back to the NO_PROXY environment variable. |
| `network.tls_strict_x509` | bool | `true` | live | v0.1.0 | Enforce the strict X.509 profile (OpenSSL VERIFY_X509_STRICT, the Python 3.13+ default) for outbound HTTPS (EGRESS-007). Set to false only when an enterprise TLS-inspection CA, or the certificates its proxy issues, cannot be reissued to meet it. False relaxes only the strict-profile rules, such as a CA's required keyUsage, critical basicConstraints, and key identifiers, lets vakr tls trust-ca install such a CA with a warning, and makes vakr check health report WARNING. It never relaxes certificate verification itself: chain signatures, validity periods, certificate purpose, and host name checks always apply. |

### `[telemetry]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `telemetry.native_retention_days` | int, >= 1 | `30` | live | v0.1.0 | Days to keep full-resolution observations; older data survives only as rollups. |
| `telemetry.rollup_retention_days` | int, >= 1 | `400` | live | v0.1.0 | Days to keep rollups, which back long-range graphs. |
| `telemetry.rollup_interval` | duration, >= 60s | `"15m"` | restart | v0.1.0 | Bucket width of historical rollups. |

### `[interface_classification]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `interface_classification.use_default_keywords` | bool | `true` | live | v0.1.0 | Include the packaged description keywords (ISP, WAN, UPLINK). Set to false to make the operator's keywords replace them instead of extending them (Appendix E.15). |
| `interface_classification.keywords` | list[table] | 3 entries, listed below | live | v0.1.0 | Interface description (ifAlias) keywords that classify interfaces (META-012). Matching is case-insensitive on whole tokens. Entries from every operator file extend the packaged list. |

#### `[[interface_classification.keywords]]`

| Field | Type | Presence | Description |
|---|---|---|---|
| `keyword` | str (pattern) | required | Whole-token keyword, matched case-insensitively; prefix re: for a bounded regular expression (POLICY-005). |
| `tag` | str | required | Tag applied to interfaces whose description contains the keyword. |
| `interest` | enum: primary \| secondary | optional | Interest class to assign (META-011). Omit it to leave interest to annotations and policy rules. |
| `wan_circuit` | bool | default false | Mark matching interfaces as WAN circuits. |
| `sticky` | bool | default false | Pin matching interfaces to the top of the device's interface list. |

Packaged entries:

| keyword | tag | interest | wan_circuit | sticky |
|---|---|---|---|---|
| `"ISP"` | `"isp"` | `"primary"` | `true` | `false` |
| `"WAN"` | `"wan"` | `"primary"` | `true` | `false` |
| `"UPLINK"` | `"uplink"` | `"primary"` | `false` | `false` |
