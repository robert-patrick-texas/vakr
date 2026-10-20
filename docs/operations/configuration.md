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
| `keyword` | str (pattern) | required | One word of letters and digits, matched case-insensitively against whole description tokens; prefix exact: to match case, or re: for a bounded regular expression that must match one whole token (POLICY-005). |
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

### `[polling]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `polling.default_interval` | duration, >= 30s, <= 3600s | `"60s"` | live | v0.1.1 | Poll interval for devices without their own interval (SCHED-010). Each poll is anchored to its intended slot, not to when the previous poll finished. The range matches the policy limits of Appendix E.18. |
| `polling.reachability_strategy` | enum: icmp_gated \| snmp_fallback \| icmp_only \| snmp_only | `"icmp_gated"` | live | v0.1.1 | How ICMP gates SNMP for devices without their own strategy (ICMP-006 to ICMP-014). icmp_gated skips SNMP after 0 or 1 of 5 replies; snmp_fallback then makes one low-cost Stage 1 attempt; icmp_only never uses SNMP; snmp_only never pings. |

### `[scheduler]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `scheduler.collector_name` | str | `""` | restart | v0.1.1 | Name of this collector; the hostname phase is hashed from it (SCHED-001, Appendix E.11). Empty means the host's short name, lower-cased, without its DNS suffix. Changing it moves every device's poll time. |
| `scheduler.phase` | enum: hostname \| fixed | `"hostname"` | restart | v0.1.1 | Where this collector's polling cycle starts within each interval (SCHED-002 to SCHED-004). hostname derives it from scheduler.collector_name, so two collectors watching the same devices do not probe them at the same instant; fixed uses scheduler.fixed_phase. |
| `scheduler.fixed_phase` | duration, >= 0s, <= 3600s | `"0s"` | restart | v0.1.1 | Cycle start offset when scheduler.phase is fixed (SCHED-004), taken modulo each device's interval. |
| `scheduler.device_order` | enum: common \| hashed | `"common"` | restart | v0.1.1 | How devices are placed after the phase (SCHED-005, SCHED-006). common hashes the device UUID, so every collector visits devices in the same relative order; hashed also hashes the collector name, giving each collector its own order. Hashing spreads polls across the interval; it does not guarantee that two devices never start together (SCHED-007). |
| `scheduler.workers` | int, >= 1, <= 1000 | `32` | restart | v0.1.1 | Polls that may run at once (SCHED-016). The MVP workload needs about 5 on average. When every worker is busy, a due poll waits up to scheduler.max_start_delay and is then recorded as not attempted, which shows as unknown, never as a device outage (SCHED-017). |
| `scheduler.queue_capacity` | int, >= 16, <= 100000 | `1000` | restart | v0.1.1 | Finished poll records held between the scheduler and the spool (section 6.4), about 10 minutes of polls at the MVP workload. When it is full, polling slows and the collector reports itself overloaded; records are never dropped. |
| `scheduler.late_after` | duration, >= 1s, <= 300s | `"5s"` | restart | v0.1.1 | A poll that starts more than this after its slot counts as late in health metrics (section 4.2 asks for 99% within about 5s). Must not exceed scheduler.max_start_delay. |
| `scheduler.max_start_delay` | duration, >= 1s, <= 600s | `"30s"` | restart | v0.1.1 | A due poll that cannot start within this time, because every worker is busy or the collector stalled, is recorded as not attempted instead of running late (SCHED-017). A device still being polled at its next slot gets that slot recorded as missed (overrun) at once (SCHED-012). |
| `scheduler.poll_deadline` | duration, >= 5s, <= 3600s | `"90s"` | restart | v0.1.1 | A poll still running after this long is stopped. Steps that already finished keep their results, and the poll is recorded as partial or failed. At the 60s default interval a hung poll costs at most one missed slot. |
| `scheduler.shutdown_grace` | duration, >= 0s, <= 300s | `"15s"` | restart | v0.1.1 | On shutdown, how long running polls get to finish before they are cancelled; every cancelled poll is still recorded, with the results of its finished steps (section 16.4). Keep it well below the service's systemd stop timeout. |

### `[icmp]`

| Option | Type | Default | Reload | Added in | Description |
|---|---|---|---|---|---|
| `icmp.probes` | int, >= 2, <= 5 | `5` | live | v0.1.1 | ICMP Echo probes sent to each device per reachability cycle (ICMP-001). At least 2: under the default icmp_gated strategy SNMP runs only after two replies, and a single probe can never show a device as reachable (Appendix E.17). At most 5, the per-probe RTT columns of Compact storage (ADR-021). |
| `icmp.spacing` | duration, >= 0.02s, <= 5s | `"200ms"` | live | v0.1.1 | Time between the starts of consecutive probes to one device (ICMP-002). Probes go out on a fixed schedule from the start of the cycle, so a late probe does not delay the next one. |
| `icmp.timeout` | duration, >= 0.1s, <= 5s | `"1s"` | live | v0.1.1 | How long to wait for each probe's reply before counting that probe as lost (Appendix E.9). With the defaults a cycle finishes within about 2 seconds. |
| `icmp.impaired_loss_percent` | float, >= 1, <= 100 | `40.0` | live | v0.1.1 | Packet loss, in percent of the probes sent, at or above which a device that still answers is shown as impaired instead of reachable (Appendix E.1). The default 40 tolerates one lost probe in five, which Control Plane Policing causes routinely. |
| `icmp.impaired_rtt_ms` | float, >= 0, <= 5000 | `0.0` | live | v0.1.1 | Median round-trip time, in milliseconds, above which a device is shown as impaired (Appendix E.1). 0, the default, turns the latency check off. |
| `icmp.max_in_flight` | int, >= 16, <= 16384 | `1024` | restart | v0.1.1 | Most ICMP probes the collector waits on at once, across all devices (Appendix E.22). A probe that cannot be sent in time is recorded as collector overload, never as packet loss. 1,024 is about twice what 100 unreachable devices need. |
