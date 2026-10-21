# Current state: `vakr status` and `vakr tui`

Two read-only views show what vakr knows right now about every device. `vakr status` prints a table, or JSON for scripts. `vakr tui` is a full-screen terminal UI for technical staff working over `ssh`. Both get the same views from the same place, so they always agree with each other. Neither changes anything on the node.

Run them on the monitoring node. Neither command ever creates a database: on a node where `metadata.db` does not exist yet, both say **no data yet** and exit with status 3.

## Where the data comes from

When [`vakr-api`](api.md) is running, both commands ask it over its local socket, `/run/vakr/api.sock` (`api.socket_path`). When it is not, they read the databases in `/var/lib/vakr` themselves. The answers are the same either way. Only the line that says where they came from differs.

| Situation | What the commands do | They say |
|---|---|---|
| `vakr-api` is running | Ask it over the socket. | `data: vakr-api socket` |
| No socket file: `vakr-api` is not enabled or was stopped | Read the databases directly. The package installs `vakr-api` disabled, so this is normal until you enable it. | `data: local databases (vakr-api is not running)` |
| `--local` given | Read the databases directly, whatever the API is doing. | `data: local databases (--local)` |
| The socket exists but nothing answers, it times out, or the answer is unusable | Stop with exit status 3. They do **not** fall back to the databases, so a crashed API is never hidden behind local reads. | `nothing answers on /run/vakr/api.sock: vakr-api stopped uncleanly or is still starting; check systemctl status vakr-api and vakr doctor, or add --local to read the databases directly` |

**Who may use the socket.** `vakr-api` serves root and members of the `vakr-admin`, `vakr-view`, and `vakr` groups, by the account that connects; no password is involved ([users](users.md#local-tui-and-cli-access)). Any other account is refused, and both commands say how to get in:

```console
$ vakr status
vakr status: this account may not use the vakr-api socket /run/vakr/api.sock: only root and members of vakr-admin, vakr-view, or vakr may; add the account to the vakr-view group (sudo usermod -aG vakr-view NAME, then log in again), or run the command with sudo
```

`vakr-view` is the group for people who only look. It gives read-only access over the socket and no access to any file. The `vakr` group is the service's own: it can also read the SNMP credentials file and `metadata.db`, with its password hashes ([where the secrets live](users.md#where-the-secrets-live)), so keep it for people you would trust with the communities. After `sudo usermod -aG vakr-view alice`, Alice must log out and in again before the new group counts.

A member of `vakr-view` can read only through `vakr-api`. When it is not running, or with `--local`, the commands stop with exit status 3 and say so, rather than reporting "no data yet" for a database they cannot see:

```console
$ vakr status
vakr status: vakr-api is not running (no /run/vakr/api.sock), and this account cannot read the databases in /var/lib/vakr directly; start it (sudo systemctl enable --now vakr-api.service vakr.target), or run the command with sudo
```

**Reading the databases directly** (`--local`, or no API) needs read access to `/var/lib/vakr`. As root, `sudo vakr status --local` first switches to the `vakr` service user, just as `vakr db` does. Running as the `vakr` user works too. The SQLite stores have no read-only connection mode, so this path opens its connections read-write. It only ever reads, without taking a write lock, and never creates a missing file.

**Limits** (Appendix E.29). Connecting to the socket may take 2 seconds, and a whole request and answer 30 seconds. An answer may be at most 16 MiB. There are no retries: `vakr status` answers or fails promptly, as a monitoring check should, and the TUI simply tries again at its next refresh.

## What a status means

Each device gets one status from the decision table in blueprint Appendix E.2. The rows are checked in order and the first match wins. The [quality states page](../architecture/quality-states.md) explains the rules in depth. Every status has an icon whose *shape* carries the meaning and a text label beside it, so the list reads the same in grayscale, for colour-blind operators, and in a log file.

| Icon | Bucket | Colour | Label | What it means |
|---|---|---|---|---|
| ✖ | Attention | red | Down | No reply of any kind (no ICMP and no SNMP) from a healthy collector with fresh data. |
| ? | Attention | amber | Unknown — *reason* | vakr cannot see the device right now. The reason says why: `no fresh observation`, `poll not attempted`, `collector degraded`, `collector overloaded`, `collector unavailable`, `no ICMP result`, or `telemetry unavailable`. **Unknown is not Down.** |
| ▲ | Attention | amber | ICMP lost, SNMP OK | Pings were lost but SNMP answered, so the device is alive. ICMP is filtered or rate-limited. |
| ▲ | Attention | amber | Packet loss / Latency | The ICMP probe group was impaired: two or three of five probes answered, or the median round-trip time was over `icmp.impaired_rtt_ms`. |
| ▲ | Attention | amber | SNMP failing | The device answers pings but SNMP timed out or returned errors. |
| ▲ | Attention | amber | Partial data | The SNMP reply was partial. |
| ▲ | Attention | amber | Restarted | The SNMP agent restarted within the last 60 minutes. |
| ▲ | Attention | amber | Reachable from *position* only | With two collectors, one sees the device as Down and the other reaches it. This is often the first sign of a network partition. |
| ✔ | Active | green | OK | Fresh evidence and no current problem. |
| ○ | Offline | gray | Disabled / Retired | An operator set the device's admin state. Retired devices are hidden unless you ask for them. |
| ◐ | Offline | gray | Maintenance (collection continues) | Still polled, but not treated as an outage. |

The list puts **Attention** at the top, **Active** in the middle, and **Offline** at the bottom. Within a bucket, the most severe status comes first (Down, then Unknown, then the degraded states), and then names in natural order, so `sw2` comes before `sw10` (Appendix E.29). Other sort orders are by name, by site, and by age (the device dark longest first).

## Reading stale, unknown, and missing values

- **"—" means not available, never zero.** A value vakr does not have, such as the latency of a device that did not answer or the rate on the first sample after a router reload, is shown as "—". `vakr status DEVICE`, the JSON output, and the device screen give the reason beside it. A measured zero, such as an idle link, is shown as `0 b/s`.
- **"(stale)"** marks a value from evidence older than the freshness threshold, which is 2.5 poll intervals and at least one interval plus 30 seconds: 150 s at the default 60 s interval. A single late poll does not make data stale; two missed polls do. A device whose newest evidence is stale is never shown as OK. It reads "Unknown — no fresh observation", and its last values stay visible, marked stale.
- **Device failure versus collector failure.** If the vakr collector is degraded, overloaded, or has stopped reporting its health, the devices it watches are shown as **Unknown — collector …**, never as Down. A collector whose latest health report is older than the freshness threshold counts as *unavailable*. In the ICMP and SNMP columns this reads "unknown (collector)".
- **Storage failure.** If the telemetry database is missing or cannot be read, the device list still appears from inventory, every enabled device reads **Unknown — telemetry unavailable**, and the header says why. If the inventory database cannot be read, the TUI keeps the rows it already has and marks them **STALE** in bold with the time they were read. It never shows them as current.
- **Reachability versus SNMP.** ICMP answers the question "does it reply to ping?" and SNMP answers "does its management agent answer?". The row shows each one separately. "ICMP lost, SNMP OK" means the device is alive.
- **Management uptime is not chassis uptime.** `sysUpTime` is how long the device's SNMP agent has run. An agent restart resets it without a reload, so a short uptime is not proof of a reboot.
- **Utilization** is shown only against a known capacity, and the capacity column names its source: `capacity override`, `ifHighSpeed`, or `ifSpeed`. Without a known speed, utilization is "—, speed unknown". It is never estimated.
- **Why a rate is missing.** The reasons include `first sample; a rate needs two`, `agent restarted; counters reset`, `interface counters were reset`, `counters changed between 32-bit and 64-bit`, `too long since the previous sample`, and `counter not reported`. vakr never subtracts across a reset, so a reload leaves a gap, not a spike.

## `vakr status`

```text
vakr status [DEVICE] [--json] [--bucket attention|active|offline ...] [--search TEXT]
            [--sort status|name|site|age] [--include-retired] [--limit N] [--offset N]
            [--local]
```

Without `DEVICE`, it prints a summary line (counts per bucket), a health line (databases and each collector's latest report), the `data:` line, and the device table. With `DEVICE`, which is the exact display name or the device UUID, it prints the device's summary, its quality warnings, its interface table in sticky, WAN, and primary order, and the reason for every "—".

`--search` matches part of the name, poll address, site, tags, collector, or status label, ignoring case: `--search down` finds the red devices. `--bucket` can be repeated. A page holds 100 devices by default and 500 at most.

### Exit status

The exit codes follow the Nagios plugin convention, so a monitoring system can run `vakr status` directly:

| Code | Meaning |
|---|---|
| 0 | Every listed device is Active or Offline. Nothing needs attention. |
| 1 | At least one listed device is in Attention (red or amber). |
| 3 | vakr cannot say: no data yet, an unreadable or unmigrated database, telemetry unavailable, an empty inventory, no such device, or a `vakr-api` that does not answer or refuses the account. |
| 2 | A usage error, such as `--limit 0`. Nagios CRITICAL is not used. |

The exit code covers the devices listed, so `vakr status --bucket attention` exits 1 exactly when something needs attention. A search that matches nothing exits 0.

A monitoring agent that runs the check under its own account, such as NRPE as `nagios`, needs that account in the `vakr-view` group (`sudo usermod -aG vakr-view nagios`, then restart the agent), and `vakr-api` running. Otherwise the check reports UNKNOWN (3) with the reason. `vakr-view` cannot read the SNMP credentials, so the agent gets nothing beyond the status itself.

### JSON

`--json` prints the same view as a JSON document with `"schema": "vakr.status/1"`, `generated_at` (ISO 8601 UTC with `Z`), `exit_code`, `source` (`"api"` or `"local"`), `health`, `counts`, and `devices` (or `device`, `facts`, `warnings`, and `interfaces` for one device). Every displayed value is an object:

```json
{"text": "—", "value": null, "reason": "no probe answered", "stale": false}
```

A missing value is `"value": null` with a `reason`, never `0`. Enum values (`attention`, `reachable`, `responding`, and so on) are stable lowercase words. A later release that removes or renames a key will also change the schema name. On failure the document is `{"schema", "exit_code", "error"}`.

## `vakr tui`

```text
vakr tui [--local] [--refresh SECONDS]
```

The TUI opens on the device list and reads current state again every `tui.refresh_interval`: 10 seconds by default, or between 2 seconds and 10 minutes with `--refresh`. Each refresh runs in the background, so the screen never freezes on a slow disk or a slow API, and the cursor stays on the same device across refreshes. A screen has at most one read in flight: typing a search asks for a refresh per key, and the ones that have not started yet are dropped in favour of the newest.

Before it opens, the TUI checks that `vakr-api` answers on the socket, and it exits with status 3 and the message above if it does not. If the API goes away while the TUI is open, the rows already shown stay, and the line under the table says **STALE** in bold, why, and when the rows were read.

| Key | Action |
|---|---|
| `/` | Search; type to filter, Enter to keep it, Escape to clear it |
| `f` | Filter by bucket: all, attention, active, offline |
| `s` | Sort: status, name, site, age |
| `r` | Refresh now |
| Enter | Open the selected device |
| Escape | Back to the list (from the device screen) |
| `h` | Health: databases and each collector's latest report and spool level |
| `?` | Help: keys and what the icons mean |
| `]` / `[` | Next / previous page of 500 devices |
| ↑ ↓ PageUp PageDown | Move |
| `q` | Quit |

The header shows the release and where the data comes from (`data: vakr-api socket`, or why the databases are read directly), the current time in the display timezone (`time.timezone`, or the host's timezone when that is empty), the health of the databases and collectors, how long the last read took, and the refresh interval. The line under the table shows the bucket counts, the filter, the sort, and the page.

The device screen shows a compact summary first: status, poll address, site, tags, and collector; ICMP probes, latency, and jitter; SNMP and `sysUpTime`; the last poll and the data's age; and any quality warnings. With two collectors, each position's own verdict is listed as well. Below the summary is the interface table: name, flags, alias, admin/oper state, in/out rate, utilization, packet rates, errors since the previous sample, capacity and its source, and sample age. Sticky interfaces come first, then WAN circuits, then primary (high-priority) interfaces, then the rest in natural name order.

The TUI is fully usable in a 120×30 terminal. In a narrower one, the least important columns are dropped first. The device list always keeps the state, name, and age columns, and the interface table always keeps the name, admin/oper state, rates, and age. Anything a hidden column holds is on the device screen and in `vakr status DEVICE`.

### Not in this release

- `--api-url` and `--profile` (blueprint 13.1) are accepted and refused. A TUI on another host would need API credentials or tokens for a person, which vakr does not offer yet. Run `vakr tui` on the monitoring node, over `ssh`.
- Interface detail with a sparkline history (blueprint 13.3) is a later addition.
- `sysDescr`, `sysObjectID`, `sysName`, `sysLocation`, and `sysContact` are not stored in inventory yet. The device screen says so; `vakr snmp discover` shows them for one device.
- State-change times are not recorded yet, so within a bucket and severity the list falls back to natural name order.
