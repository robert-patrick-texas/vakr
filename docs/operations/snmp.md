# SNMP polling and discovery

vakr reads devices over SNMPv2c (SNMPv3 follows after the MVP). It does three things over SNMP:

- **Discovery** reads what a device says about itself: the system group, every interface (name, description, type, speed, MAC, state), and the interface IP addresses. It runs when a device is added and every 24 hours after that (`snmp.rediscovery_interval`).
- **Stage 1** runs on every poll, every 60 seconds by default. It reads `sysUpTime.0` and the admin and operational state of each known interface, by exact OID. It never walks whole tables.
- **Stage 2** reads traffic, packet, and error counters, but only for interfaces that are *admin up and oper up right now*. Interfaces that are down or shut are still inventoried and status-checked; there is just nothing to count.

Each device gets at most one SNMP request in flight at a time, because weak agents drop or time out parallel requests. A request carries at most 30 values and is halved automatically if the device answers `tooBig`.

## Credentials: by name only

Devices never hold a community. They name a credential in `/etc/vakr/secrets/credentials.toml`, for example `network-v2c`. See [credentials](credentials.md) for the file, its permissions, and rotation. vakr reads the community from that file when it opens each SNMP session and forgets it when the session closes. It never logs a community, never puts one in an error message, and never accepts one on the command line: `vakr snmp` refuses `--community` and `-c` without repeating the value. If you typed a real community there, it is in your shell history, so rotate it.

## Preparing Cisco IOS XE devices

A minimal read-only configuration, limited to the vakr collectors:

```text
ip access-list standard VAKR-SNMP
 permit 192.0.2.10                          ! each vakr position
snmp-server community <community> RO VAKR-SNMP
snmp-server ifindex persist
snmp ifmib ifalias long
snmp-server location <site and rack>
snmp-server contact <team>
```

Things worth knowing on IOS XE:

- **`snmp ifmib ifalias long`** lets `ifAlias` carry the full interface description instead of the first 64 characters. vakr classifies interfaces from description keywords (ISP, WAN, UPLINK; see [policies](policies.md)), so a truncated description can lose its keyword.
- **`snmp-server ifindex persist`** keeps `ifIndex` numbers across reloads. vakr does not depend on it, because it tracks interfaces by its own identity, but it means fewer renumberings to reconcile.
- **Tunnel interfaces report 100 kb/s** (`ifSpeed` 100000, `ifHighSpeed` 0) unless `bandwidth` is configured. vakr uses that speed as the utilization denominator. Real tunnel traffic then shows as far above capacity, and the rates are flagged `RATE_EXCEEDS_CAPACITY` rather than clipped. Configure `bandwidth`, or set a capacity override on the interface.
- **10 Gb/s and faster ports report `ifSpeed` 4294967295**, the 32-bit maximum. vakr uses `ifHighSpeed` (in Mb/s) for those, and `ifSpeed` only below 20 Mb/s, where it is more precise.
- **Catalyst SVIs (`VlanN`) all report the chassis MAC.** A MAC address is evidence about an interface, not proof of which one it is.
- **`ifCounterDiscontinuityTime`** is missing on many interfaces and releases. vakr notices, stops asking for it, and checks again after `snmp.capability_reprobe_interval` (7 days). The same applies to interfaces without 64-bit (HC) counters, which then fall back to 32-bit counters.
- **Addresses** come from `ipAddressTable` (IPv4 and IPv6). If a device lists no IPv4 addresses there, vakr falls back to the older IPv4-only `ipAddrTable`.
- **Control-plane policing** can rate-limit SNMP the way it rate-limits ICMP. vakr's one-request-at-a-time, 30-value limit keeps a poll of a 48-port switch to about 18 small requests.
- **An `snmp-server view`** that excludes IF-MIB or IP-MIB makes those tables look absent. `vakr snmp discover` shows each table's status, so you can see which one.

## Checking a device: `vakr snmp discover` and `vakr snmp poll`

Both commands talk to one device directly, using the same code the collector uses, and print what they find in plain language.

```console
$ sudo vakr snmp discover 192.0.2.1 --credential network-v2c
SNMP discovery of 192.0.2.1:161 with credential 'network-v2c' (SNMPv2c)
The agent answered: 18 requests, 18 answered, 0 timed out, 0 retries, 0.21 s waiting.

  sysName      rtr1.example.net
  sysDescr     Cisco IOS XE Software, Version 17.12.04
  sysObjectID  1.3.6.1.4.1.9.1.2734
  sysLocation  Lab site 2, rack 1
  sysContact   noc@example.net
  sysUpTime    0 d 02:48:00 (management-agent uptime; an agent restart resets it, so it does not prove chassis uptime)

  system          complete  6 values
  ifTable         complete  104 values
  ifXTable        complete  64 values
  ipAddressTable  complete  24 values
  ipAddrTable     skipped   not needed: ipAddressTable listed the IPv4 addresses

13 interfaces discovered, 10 admin up and oper up:
IFINDEX  NAME                  TYPE  ADMIN  OPER  SPEED   COUNTERS   DESCRIPTION        ADDRESSES
1        GigabitEthernet0/0/0  6     up     up    1 Gb/s  HC 64-bit  ISP ckt 4711       192.0.2.0/31, 2001:db8:0:1::1/64
...
```

`vakr snmp poll` runs discovery and then one Stage 1/Stage 2 poll, and shows the raw counters. Counters are cumulative, so with `--rate-after SECONDS` (1 to 300) it polls a second time and shows traffic rates, computed exactly as the collector computes them. A rate that cannot be computed says why, for example "the SNMP agent restarted between the samples". It never shows 0.

| Option | Meaning |
|---|---|
| `TARGET` | IP address or DNS name of the device |
| `--credential NAME` | Credential name in the credentials file (required) |
| `--port`, `--timeout`, `--retries` | Override `snmp.port`, `snmp.timeout` (1 to 30 s), and `snmp.retries` (0 to 5) |
| `--family ipv4\|ipv6`, `--source ADDRESS` | Address family for a DNS name; local address to send from |
| `--dev` | Development only: warn about unsafe credential-file permissions instead of refusing |

| Exit status | Meaning |
|---|---|
| 0 | The device answered everything |
| 1 | The device answered, but some data did not arrive (shown as `?`) |
| 2 | Usage error or invalid target (for example a multicast address) |
| 4 | No usable SNMP answer from the device |
| 5 | A collector problem: the credential, DNS, or the collector's own socket |

## Reading the results

- **SNMP answering is not the same as reachable.** A device can answer ping and not SNMP: a wrong community, an ACL, a restricted view, or an overloaded agent. When SNMP gets no answer, vakr says so; it does not say the device is down. Reachability comes from ICMP.
- **`sysUpTime` is the management agent's uptime.** An SNMP agent restart resets it without the chassis rebooting. vakr reports a drop as an agent restart, never as a proven reboot. The counter also wraps to 0 every 497 days, and vakr recognizes that as continuous.
- **Missing is not zero.** A value the device did not return is shown as `?` and stored as missing. The quality flags say why: `MISSING_FIELDS` when the device said "no such object", and `PARTIAL_RESPONSE` when a request timed out or failed.
- **Collector problems are not device problems.** A missing credential, a DNS failure, or the collector's own socket failing makes the SNMP state *unknown* (`COLLECTOR_DEGRADED`), never *timeout*. One misconfigured collector therefore cannot paint every device red.
- **Effort is recorded.** Each SNMP observation keeps its requests, answers, timeouts, and retries. `RETRIED` means a request needed a second attempt. `REQUEST_SIZE_REDUCED` means the device answered `tooBig` and vakr halved its requests.
- **Rates need a valid baseline.** After an agent restart, a counter discontinuity, a change between 32-bit and 64-bit counters, or a long gap, the first sample only starts a new baseline. The graph shows a gap, not a spike.

## Settings

All options live under `[snmp]`; see the [configuration reference](configuration.md#snmp) for the full table and limits. The defaults come from blueprint Appendix E.4, E.9, and E.23.

```toml
[snmp]
timeout = "2s"                        # per attempt, 1s to 30s
retries = 1                           # complete new attempts after a timeout, 0 to 5
max_varbinds = 30                     # per request; halves after tooBig, down to 5
max_repetitions = 25                  # GETBULK rows per discovery request
rediscovery_interval = "24h"
capability_reprobe_interval = "7d"    # retry counters a device said it lacks
```

## What vakr reads (OID catalog)

These are the standard objects of blueprint 9.4, from SNMPv2-MIB (RFC 3418), IF-MIB (RFC 2863), and IP-MIB (RFC 4293). "Fixture" names the interfaces of the simulated fleet (`tests/simulators/snmp/ports.py`) that exercise each case.

| Object | OID | Type and units | Stage | Fallback and notes | Fixture |
|---|---|---|---|---|---|
| `sysDescr.0` | 1.3.6.1.2.1.1.1.0 | DisplayString | Discovery | Multi-line on IOS XE; the first line is shown | all devices |
| `sysObjectID.0` | 1.3.6.1.2.1.1.2.0 | OID | Discovery | Platform hint | routers, switches |
| `sysUpTime.0` | 1.3.6.1.2.1.1.3.0 | TimeTicks (0.01 s) | Discovery, Stage 1 | Agent uptime; wraps at 2^32 (497 days); continuity per E.5 | all devices; agent restart |
| `sysContact.0`, `sysName.0`, `sysLocation.0` | 1.3.6.1.2.1.1.4.0, .5.0, .6.0 | DisplayString | Discovery | Display only | all devices |
| `ifDescr` | 1.3.6.1.2.1.2.2.1.2 | DisplayString | Discovery | Identity evidence | all |
| `ifType` | 1.3.6.1.2.1.2.2.1.3 | IANAifType | Discovery | Decides "physical": ports yes; loopback, SVI, tunnel, subinterface, LAG no; others unknown | `Null0` (unknown), `Gi0/0/0.100` |
| `ifMtu` | 1.3.6.1.2.1.2.2.1.4 | Integer (octets) | Discovery | | `Tunnel10` (9976) |
| `ifSpeed` | 1.3.6.1.2.1.2.2.1.5 | Gauge32 (b/s) | Discovery | 4294967295 means saturated; used below 20 Mb/s | `Te0/1/0`; tunnels at 100 kb/s |
| `ifPhysAddress` | 1.3.6.1.2.1.2.2.1.6 | octets | Discovery | Empty on logical interfaces; SVIs share the chassis MAC | switch `VlanN` |
| `ifAdminStatus`, `ifOperStatus` | 1.3.6.1.2.1.2.2.1.7, .8 | enum | Discovery, Stage 1 | Only up/up interfaces go to Stage 2; unknown is not up | `Gi0/0/3` (down/down), `Te0/1/2` (notPresent) |
| `ifLastChange` | 1.3.6.1.2.1.2.2.1.9 | TimeTicks | Discovery | State context | all |
| `ifInOctets`, `ifOutOctets` | 1.3.6.1.2.1.2.2.1.10, .16 | Counter32 (octets) | Stage 2 | Used only where HC counters are missing; wraps per E.4 | `Tunnel20` |
| `ifInUcastPkts`, `ifOutUcastPkts` | 1.3.6.1.2.1.2.2.1.11, .17 | Counter32 | Stage 2 | 32-bit packet component | `Tunnel20` |
| `ifInErrors`, `ifOutErrors` | 1.3.6.1.2.1.2.2.1.14, .20 | Counter32 | Stage 2 | 32-bit only, even on HC interfaces | router `Gi0/0/0` (rising) |
| `ifName` | 1.3.6.1.2.1.31.1.1.1.1 | DisplayString | Discovery | Display and identity | all |
| `ifIn/OutMulticastPkts`, `ifIn/OutBroadcastPkts` | 1.3.6.1.2.1.31.1.1.1.2 to .5 | Counter32 | Stage 2 | 32-bit packet components | `Tunnel20` |
| `ifHCInOctets`, `ifHCOutOctets` | 1.3.6.1.2.1.31.1.1.1.6, .10 | Counter64 (octets) | Discovery probe, Stage 2 | Preferred; absence is learned and 32-bit is used | all but `Tunnel20` |
| `ifHCIn/Out{Ucast,Multicast,Broadcast}Pkts` | 1.3.6.1.2.1.31.1.1.1.7 to .9, .11 to .13 | Counter64 | Stage 2 | Summed into one total per direction and not stored separately (ADR-006); a missing component leaves the total missing | all HC interfaces |
| `ifHighSpeed` | 1.3.6.1.2.1.31.1.1.1.15 | Gauge32 (Mb/s) | Discovery | Preferred speed source | `Te0/1/0` (10000) |
| `ifAlias` | 1.3.6.1.2.1.31.1.1.1.18 | DisplayString | Discovery | Description keywords (META-012); see `ifalias long` above | router `Gi0/0/0` (ISP), `Gi0/0/1` (UPLINK) |
| `ifCounterDiscontinuityTime` | 1.3.6.1.2.1.31.1.1.1.19 | TimeTicks | Discovery probe, Stage 2 | A change invalidates that interface's rate baseline | router `Gi0/0/1` only |
| `ipAddressIfIndex`, `ipAddressType`, `ipAddressPrefix` | 1.3.6.1.2.1.4.34.1.3, .4, .5 | Integer, enum, OID | Discovery | IPv4 and IPv6; prefix length from the pointer into `ipAddressPrefixTable` (1.3.6.1.2.1.4.32); broadcast rows ignored | router `Gi0/0/0` (one IPv4, two IPv6) |
| `ipAdEntIfIndex`, `ipAdEntNetMask` | 1.3.6.1.2.1.4.20.1.2, .3 | Integer, IpAddress | Discovery | IPv4 fallback when `ipAddressTable` has no IPv4 | `rtr-sim-009` (legacy variant) |

## The simulator, for development

The repository includes a synthetic fleet shaped like the MVP reference workload: 80 IOS XE-like routers and 20 Catalyst-like switches, 1,600 up/up interfaces. It uses only documentation addresses (RFC 5737, RFC 3849) and `example.net` names. Communities are generated for every run and never stored in the repository.

- **Serve it** and try the commands against it:

  ```console
  $ uv run python -m tests.simulators.snmp.serve --routers 2 --switches 1
  snmpsim serves 3 devices on 127.0.0.1:41161. Try:
    VAKR_ROOT=/tmp/vakr-snmpsim-x uv run vakr snmp discover 127.0.0.1 --port 41161 --credential rtr-sim-001 --dev
  ```

- **Tests** run against two servers. Real [snmpsim](https://pypi.org/project/snmpsim/) checks vakr against an independent agent. An in-process fault agent (`tests/simulators/snmp/agent.py`) misbehaves on cue: tooBig, timeouts, error statuses, restarts, and changed counters. Both run with `uv run pytest tests/simulators/snmp`. ADR-034 explains the split.
- **Load check**: `make snmp-load` discovers and polls the full 100-device fleet. It needs about 30 s and must finish within one 60 s interval.
- **Field captures** become simulator data with [walk2rec](../../tools/snmpsim/README.md). The [field kit](field-data.md) collects `snmpbulkwalk` output from production devices, and `walk2rec` turns it into a `.snmprec` file that snmpsim serves.
