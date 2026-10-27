# Observation spool

Every observation a collector makes (ICMP, SNMP, interface counters, poll records, its own health) is written to a **spool** on local disk before it goes to the telemetry database. The spool is what lets polling carry on while the database is slow, locked, full, or being restored, and what keeps a crash or power cut from silently losing telemetry. This page covers where the spool lives, how big it gets, what vakr does when it fills up, and what each recovery message means.

The short version:

- A collector considers an observation handed off only after the spool has written it **and fsynced it** (once a second at the most, or every 1,000 observations).
- Ingestion copies spooled observations into the telemetry database every 10 seconds and deletes spool files only after the database has accepted every record in them.
- If anything is ever lost, vakr writes a **telemetry-loss incident** saying what, when, and how many. It never drops data silently, and it never reports a collector or storage problem as a device being down.

## Where it lives

| Path | What it is |
|---|---|
| `/var/lib/vakr/spool/<collector UUID>/` | One collector's spool. Each collector has its own directory, and only that collector reads and writes it. |
| `…/seg-<number>.spool` | Segment files: append-only, at most 64 MiB each. The number is the sequence number of the first record. |
| `…/seg-<number>.damaged` | A segment in which vakr found a checksum failure. Records before the damage are still ingested. See [recovery messages](#recovery-messages). |
| `…/consumer.offset` | The last record the telemetry database has durably accepted. |
| `…/writer.state` | Whether the collector stopped cleanly last time. |
| `…/incidents.jsonl` (and `incidents.1.jsonl`) | Telemetry-loss incidents, one JSON object per line. Rotated at 1 MiB. |
| `…/status.json` | The collector's latest spool and ingestion health, rewritten every ingest cycle. `vakr check health` reads it. |
| `…/quarantine/` | Files vakr kept but cannot use: damaged segments, records no release could decode, and batches the database rejected. vakr never deletes these. |
| `…/lock` | Held while a collector has the spool open, so two processes cannot write it. |

Change the root with `spool.directory`, for example to put the spool on a separate disk. Use a disk-backed filesystem. vakr refuses paths under `/dev/shm` and `/run`, because tmpfs loses everything at power-off and so can never be the only copy of unstored telemetry (STORE-005).

Everything under the spool directory belongs to the `vakr` user. Don't edit, move, or delete segment files while vakr runs. If you need the space back, see [a full spool](#when-the-spool-fills-up).

## Sizing

| Option | Default | Meaning |
|---|---|---|
| `spool.max_size_mib` | 4096 | Most disk the spool may use, quarantine included. |
| `spool.segment_size_mib` | 64 | When to start a new segment file. |
| `spool.fsync_interval` | `1s` | Longest an observation waits for the fsync that makes it durable. |
| `spool.fsync_records` | 1000 | Fsync early once this many records are waiting. |
| `spool.queue_capacity` | 10000 | Observations that may wait in memory for that fsync. |
| `spool.warning_pct`, `critical_pct`, `exhausted_pct` | 70, 85, 95 | Fill levels for WARNING, CRITICAL, and EXHAUSTED. |
| `spool.ingest_interval` | `10s` | How often ingestion writes a batch to the database. |
| `spool.ingest_max_rows` | 50000 | Most observations in one database write. |

The reference MVP workload (100 devices, about 1,600 active interfaces, 60-second polling) produces about 1,900 observations a minute, roughly 108 MB of spool an hour. While the database keeps up, the spool holds well under one segment. When the database stops accepting writes, the spool grows at that rate, so the default 4 GiB covers about **40 hours** of database outage. To cover a long weekend, set `spool.max_size_mib` to about 108 MB per hour of outage you want to survive, scaled by your device count, and make sure the filesystem has that much free space. The health check warns when the filesystem has less free space than the spool may still use.

All spool options need a service restart. `vakr config validate` checks each value. It doesn't check that the thresholds are in order, or that the limit holds at least four segments. A collector refuses to start on such a combination, and `vakr check health` reports it as CRITICAL.

## When the database is unavailable

Polling continues. Observations accumulate in the spool, and ingestion retries the oldest batch: up to five attempts per cycle with backoff from 1 to 60 seconds, then again the next cycle, for as long as it takes. When the database comes back, ingestion catches up in full batches of `spool.ingest_max_rows`, oldest first. Records that were stored just before a failure are offered again. The database recognises them and skips them, so nothing is stored twice.

`vakr check health` shows, for example:

```console
VAKR WARNING - spool: 212 MiB (5.2%) used; telemetry database not accepting writes (StorageError: telemetry database is locked); polling continues, oldest unacknowledged record 1940s old | spool_bytes=222298112.0B spool_pct=5.18% spool_oldest_s=1940.3s spool_incidents=0.0
```

This is the collector's own condition. Devices keep their real state, and graphs fill in once the backlog is stored.

## When the spool fills up

| State | Fill | What happens |
|---|---|---|
| NORMAL | below 70% | Nothing to do. |
| WARNING | 70% | `vakr check health` reports WARNING. Find out why the database is not keeping up. |
| CRITICAL | 85% | `vakr check health` reports CRITICAL capacity degradation. |
| EXHAUSTED | 95% | The collector stops scheduling new normal polling instead of losing observations (6.4). Devices it doesn't poll show as *not attempted* and unknown, never down. The last 5% absorbs observations already in flight. |
| Full | 100% or a full filesystem | Appends fail. The spool records a `rejected_full` incident with the exact number, types, and time range of the observations it could not keep. |

To recover, fix the database first. Ingestion then drains the spool and polling resumes on its own once the fill level drops below 95%. If you must free space before that, remove files from `quarantine/` that you no longer need for a support case, or move the whole spool directory to a bigger disk while the collector is stopped. Never delete `seg-*.spool` files: they are telemetry that is not yet stored.

## Recovery messages

On every start the collector checks its spool before polling. Each finding is logged to the journal at ERROR with event ID `spool.loss` and appended to `incidents.jsonl`. `vakr check health` stays at WARNING for 24 hours after an incident.

```console
$ journalctl -u vakr-collector | grep 'telemetry loss'
telemetry loss (unclean_stop): 1904 observation(s) (estimate), sequence 88231-None, classes device_poll,icmp,interface,snmp_device: the previous collector run stopped without closing the spool; ...
```

| Kind | What happened | What to do |
|---|---|---|
| `unclean_stop` | The collector was killed or the host lost power. Observations completed after the last fsync may be gone. The count is an estimate: the most records any one second (`spool.fsync_interval`) held among the newest records. The time range runs from the last durable record to the restart, because when the collector died is not known. | Nothing, beyond finding out why it stopped. This is at most about one second of telemetry (E.8). |
| `torn_tail` | Power was lost during a write. vakr cut off the half-written record at the end of the newest segment. | Nothing. |
| `damaged_segment` | A record failed its checksum somewhere other than the end, which means the disk or something outside vakr changed bytes. vakr renamed the file `.damaged`, stored the records before the damage, and moves the file to `quarantine/` once those are stored. | Check the disk (`smartctl`, `dmesg`). Keep the quarantined file if you want support to look at it. |
| `bad_header` | A segment's header was unreadable. The whole file went to `quarantine/`. | As for `damaged_segment`. |
| `missing_range` | Records are missing between two segments or before the oldest one, usually because a file was deleted by hand. The count is exact. | Don't remove spool files. |
| `rejected_full` | The spool or its filesystem was full. The count is exact. | See [a full spool](#when-the-spool-fills-up). |
| `write_failed` | A write or fsync failed for a reason other than space, such as an I/O error. | Check the disk. |
| `undecodable` | A record's envelope failed its own SHA-256 or has a schema newer than this release, for example after a downgrade. It was copied into `quarantine/undecodable-*.spool`. | Upgrade, or keep the file for support. A later release can read it. |
| `rejected_by_storage` | The database refused a batch as invalid. It was copied into `quarantine/rejected-*.spool`. | Report it. This is a bug. |

Each incident line records the sequence range, the observation time range in microseconds since the epoch (`start_us`, `end_us`), the observation types, the count, and whether the count is exact.

## File format

For tools and support. A segment starts with a 32-byte header: the magic `VAKRSPOL`, format version 1 (16-bit), header size, the first record's sequence number (64-bit), the creation time in epoch microseconds, and a CRC-32 of the preceding bytes. Then records follow, each with a 40-byte frame header:

| Offset | Bytes | Field (big-endian) |
|---|---|---|
| 0 | 4 | Payload length, 1 byte to 1 MiB |
| 4 | 4 | CRC-32 (IEEE, as `zlib.crc32`) of bytes 0–3, bytes 8–39, and the payload |
| 8 | 8 | Sequence number, contiguous across the spool |
| 16 | 8 | Append time, microseconds since the Unix epoch (UTC) |
| 24 | 16 | Batch UUID, shared by the records of one append |
| 40 | n | The observation envelope in its canonical JSON form, which has its own SHA-256 ([domain model](../architecture/domain.md)) |

The blueprint names MessagePack for the payload. vakr stores the existing JSON envelope instead, so there is a single envelope format; [Appendix E.24](../../tool-blueprint.md#e24-spool-and-ingestion-store-004-to-store-011-64-e8) records the decision. Any change to this layout bumps the format version, and a new release keeps reading older segments (6.3).

## See also

- [Configuration reference](configuration.md) for every `spool.*` option.
- [Blueprint 6.4](../../tool-blueprint.md#64-backpressure-and-storage-exhaustion) for the backpressure and capacity rules.
- [Telemetry storage](../architecture/storage.md) for what happens after ingestion.
