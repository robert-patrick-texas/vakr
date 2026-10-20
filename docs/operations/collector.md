# The collector service

`vakr-collector.service` is the process that does the monitoring. On a Compact node it is the whole pipeline: it pings every device, polls it over SNMP, discovers its interfaces, works out traffic rates, makes every result durable in the [spool](spool.md), and writes it into the telemetry database. It is the only process that writes telemetry. The API and web UI only read it.

This page covers starting and stopping the collector, what its startup checks mean, where its state lives, and how to read its log.

## Starting, stopping, and reloading

The package installs the unit but never enables or starts it, so a fresh install does not begin polling before you have told vakr what to poll (PKG-003). Once inventory exists, enable the collector and the common target:

```console
$ sudo systemctl enable --now vakr-collector.service vakr.target
```

From then on `vakr.target` starts the collector at boot, and `systemctl restart vakr.target` (which upgrades use) restarts it. A collector started with an empty inventory doesn't fail. It runs, reports itself healthy, and starts polling as soon as devices appear.

| Task | Command | What happens |
|---|---|---|
| Start | `sudo systemctl start vakr-collector` | The startup checks run. The unit counts as started only once they pass and polling has begun. |
| Stop | `sudo systemctl stop vakr-collector` | A graceful drain, described below. It normally takes a few seconds and never more than about a minute. |
| Restart | `sudo systemctl restart vakr-collector` | Stop, then start. Traffic rates carry on across the restart. |
| Reload | `sudo systemctl reload vakr-collector` | Re-reads `/etc/vakr`. An invalid file is refused, and the running settings stay. |
| Status | `systemctl status vakr-collector` | Shows whether it is running and its last status line. |

**Reload** applies the options marked `live` in the [configuration reference](configuration.md) at once: ICMP probing and thresholds, SNMP timeouts and request sizes, rediscovery intervals, the `[rates]` limits, and the `[polling]` defaults. Options marked `restart`, such as `scheduler.workers` or the spool sizes, take effect only after a restart. The log names them. Inventory changes need no reload, because the collector notices them within five seconds.

**Stopping** follows a fixed order, so nothing that was collected is lost:

1. Discovery stops, and no new poll starts.
2. Polls already running get `scheduler.shutdown_grace` (15 seconds) to finish. Any still running after that are cancelled and recorded with whatever they had already measured.
3. Every finished result goes into the spool, followed by one last health report.
4. The spool is fsynced and marked as cleanly stopped.
5. Ingestion writes whatever is left in the spool to the database. If the database can't take it now, it stays in the spool for the next start.
6. The rate baselines are saved (see below), and the databases are closed.

The unit allows 120 seconds for this. The log line `collector stopped in N s` reports how long it actually took.

## Startup checks

Before it discovers or polls anything, the collector checks that it can run safely (blueprint 16.3). If a check fails, it logs one sentence that names the check and the fix, then exits with status 78, and systemd does not restart it, because retrying cannot fix any of these.

| Check | Fails when | What to do |
|---|---|---|
| `config` | A file in `/etc/vakr` has a syntax error, an unknown key, or a bad value, or two options contradict each other. | Run `vakr config validate`, which lists every problem with its file and line. |
| `state_dir` | `/var/lib/vakr` is missing or not writable by the `vakr` user. | Reinstall the package, or run `sudo systemd-tmpfiles --create vakr.conf`. |
| `metadata_db`, `telemetry_db` | A database is missing, at an older schema, or at a newer schema than this release. | Older or missing: run `sudo vakr db migrate`. Newer: this release refuses to run on it (MIG-004). Upgrade vakr, or restore the backup taken before the downgrade. `vakr db status` shows the details. |
| `credentials` | `/etc/vakr/secrets/credentials.toml` has unsafe permissions or is invalid. | Run `vakr credentials check`. See [credentials](credentials.md). |
| `identity` | This node's collector identity conflicts with the metadata database (see below). | Follow the message. |
| `spool` | Another collector process holds this collector's spool, or the spool settings are invalid. | Stop the other process. Two collectors on one host each need their own `scheduler.collector_name`. |
| `icmp` | This host does not let the collector send ICMP Echo: `net.ipv4.ping_group_range` excludes the `vakr` group, and no `CAP_NET_RAW` drop-in is installed. It runs first, before any database is opened. | Apply the fix that `vakr doctor` prints, then `sudo systemctl restart vakr-collector`. See [ICMP](icmp.md). |

If the host stops allowing ping while the collector runs, the collector logs `collector.icmp_refused` and stops with status 78 too.

One condition only produces a warning, because collection still works:

- **No credentials file yet.** ICMP runs, and every SNMP poll is recorded as a collector-side credential problem, never as the device being down.

## What the collector does while it runs

- **Polls** each device on its own anchored slot, every `polling.default_interval` (60 seconds) or the device's own interval: ICMP first, then SNMP Stage 1 (uptime and interface status), then Stage 2 (counters) for interfaces that are up/up now.
- **Discovers** a device that has no poll plan yet within about a minute of it appearing, and rediscovers every device every `snmp.rediscovery_interval` (24 hours), spread evenly across the day and at the same time of day across restarts. At most two discoveries run at once, beside polling. A successful discovery is repeated only after the rediscovery interval, even for a device that reports no interfaces. A discovery that fails is retried after 1 minute, then 2, 4, 8, 16, and 32 minutes, and then hourly until it succeeds. Meanwhile the device is still pinged and its SNMP uptime read, so its reachability shows from the start.
- **Derives rates** from consecutive samples of each interface, using the actual time between them. The first sample of an interface has counters but no rates (`BASELINE_FIRST_SAMPLE`), and so does the sample after a counter reset or an agent restart. Missing data stays missing, never 0.
- **Reports its own health** once at start, and then at least once per poll interval. A collector that stops reporting shows as unavailable in the views, and its devices as unknown, never as down.
- **Pauses polling when the spool is full.** At EXHAUSTED, due polls are recorded as not attempted (unknown) until ingestion catches up. See [the spool guide](spool.md).
- **Maintains the telemetry database**: it moves staged rows into their day tables every 15 minutes and runs the retention pass every hour.

To see what the collector would do with one device right now, run a single diagnostic cycle. It uses the same configuration and plan, writes nothing, and is safe to run beside the service:

```console
$ sudo -u vakr vakr collector once core1
core1 (192.0.2.1) polled once as collector vakr-east: icmp_gated, every 60 s
ICMP: 5/5 replies, median 1.204 ms (min 1.100 ms, max 1.380 ms, jitter 0.090 ms): reachable
SNMP: responding; agent uptime 41 d 03:12:09 (management uptime); 4 requests, 0 timeouts
Interfaces: 52 in the poll plan, 10 sampled (up/up and collected)
...
Verdict: OK (green, Appendix E.2 row 10)
```

Its exit status follows the Nagios convention: 0 OK, 1 warning, 2 down, 3 unknown (a collector-side problem), and 78 when the configuration, database, or device name can't be used.

## Where state lives

| Path | What it is |
|---|---|
| `/var/lib/vakr/collector/identity.json` | This node's collector UUID. Every observation, the spool directory, and the collector's record in the metadata database use it. Don't delete it: a new UUID would start a new history. |
| `/var/lib/vakr/collector/<UUID>/rate-baselines.json` | The latest counter sample of every interface, and each device's last uptime reading. It is rewritten atomically every minute and at stop, so a restart costs no rate interval. If it is missing or damaged, rates restart after two polls, and nothing is wrong. |
| `/var/lib/vakr/spool/<UUID>/` | The spool. See [the spool guide](spool.md). |
| `/var/lib/vakr/metadata.db`, `telemetry.db` | The databases. See [databases](database.md). |

**Identity.** On first start the collector registers itself in the metadata database under `scheduler.collector_name` (the host's short name by default) and writes `identity.json`. If the state directory is lost but the metadata database survives, the collector adopts the registered UUID of its name again. If `identity.json` names one UUID but the metadata database has the same collector name under another UUID, the collector refuses to start (`identity`), because merging them would mix two histories. Restore the right `identity.json`, or give this node another `scheduler.collector_name`.

## Reading the log

The collector logs to the journal. Each line starts with a stable event ID, so you can search for one kind of event:

```console
$ journalctl -u vakr-collector -n 50
$ journalctl -u vakr-collector | grep 'collector.preflight_failed'
```

| Event ID | Meaning |
|---|---|
| `collector.started` | The startup checks passed and polling began. |
| `collector.preflight_failed` | A startup check failed. The line names the check and the fix. |
| `collector.preflight_warning` | A startup condition that does not stop collection, such as no credentials file. |
| `collector.icmp_refused` | The host refused ICMP Echo while the collector ran; it stopped with status 78 until the sysctl is fixed. |
| `collector.replanned` | Inventory changed: how many devices are planned, added, removed, and kept. |
| `collector.reloaded`, `collector.reload_refused` | `systemctl reload` applied the new configuration, or refused an invalid one. |
| `collector.inventory_unavailable` | The metadata database could not be read. Polling continues with the last plan. |
| `discovery.no_result`, `discovery.recovered` | A device's discovery got no answer (logged once until it works again), and when it does. |
| `collector.spool_refused` | The spool refused observations. A telemetry-loss incident records exactly what (see [the spool guide](spool.md)). |
| `ingest.write_failed` | The telemetry database refused a write. Observations stay in the spool and are retried. |
| `telemetry.move_failed`, `telemetry.retention_failed` | A maintenance pass failed. It runs again later, and no data is lost. |
| `collector.checkpoint_failed` | The rate baselines could not be saved. A restart now would cost one rate interval. |
| `collector.component_failed` | An internal part stopped unexpectedly. The collector shuts down cleanly, exits 1, and systemd restarts it (at most five times in ten minutes). |
| `collector.stopped` | The graceful stop finished, and how long it took. |

No log line ever contains a community or any other secret.

## Resource limits

The unit runs as the `vakr` user with no capabilities, a read-only view of the system except `/var/lib/vakr`, at most 2 GiB of memory, 512 tasks, and 16,384 open files. That is several times what 100 devices need. The only supported way to grant the collector a raw-socket capability is the ICMP drop-in described in [ICMP](icmp.md). No other vakr unit may have it.

The defaults on this page are listed in blueprint Appendix E.27.
