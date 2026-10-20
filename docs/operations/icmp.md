# ICMP reachability

vakr checks that each device answers ICMP Echo ("ping") once per polling cycle, the way SmokePing does. It sends **five probes 200 ms apart** and waits **up to 1 second** for each reply ([Appendix E.9](../../tool-blueprint.md#e9-collection-timing)). Every probe's round-trip time is kept, so graphs can show the spread of latency and loss, not just an average ([ICMP-003](../../tool-blueprint.md#86-icmp-collection-and-reachability-requirements)).

This page covers what the results mean, the one host setting ICMP needs, the fallback for hosts that cannot use it, and the `vakr icmp probe` command. The design is recorded in [ADR-023](../adr/ADR-023-unprivileged-icmp.md).

## What the results mean

| Replies of 5 | State | SNMP this cycle (default `icmp_gated`) |
|---|---|---|
| 5 | Reachable | collected |
| 4 | Reachable, flagged `PARTIAL_LOSS` | collected |
| 3 or 2 | Impaired | collected |
| 1 | Impaired, flagged `SINGLE_REPLY` | skipped |
| 0 | Unreachable, or Unknown if the collector itself had trouble | skipped |

Losing one probe in five is routine: Cisco IOS XE Control Plane Policing rate-limits ICMP to the route processor. vakr therefore keeps 4 of 5 green and reserves Unreachable for zero replies ([Appendix E.1](../../tool-blueprint.md#e1-icmp-reachability-state-five-probes)). Two settings change the thresholds: `icmp.impaired_loss_percent` (default 40) and `icmp.impaired_rtt_ms` (median RTT, off by default). See the [configuration reference](configuration.md#icmp).

A few distinctions matter when you read these states:

* **Reachability is not SNMP availability.** A device can answer ping and fail SNMP, or, under the opt-in `snmp_fallback` strategy, answer SNMP while ICMP is filtered. vakr shows both, and never turns ICMP loss into success because SNMP worked.
* **A lost probe is stored as `-1`, never as 0 ms.** APIs show it as missing. Lost probes never enter the latency or jitter figures. Jitter needs at least two replies.
* **A device failure is not a collector failure.** If vakr could not send a probe, or could not watch for its reply, that probe is collector evidence, not loss. Reasons include missing permission, a local routing or firewall refusal, too many probes in flight, and a full receive buffer. With no replies, such a cycle is **Unknown**, never Unreachable, so a collector problem cannot turn every device red ([invariant 2](../../tool-blueprint.md#43-non-negotiable-correctness-invariants)).
* **Unknown means "not observed from this collector".** It is not "down".

When a router answers a probe with "host unreachable" or "time exceeded", the probe counts as lost, and `vakr icmp probe` shows which router said so.

## Permissions: `net.ipv4.ping_group_range`

vakr sends ICMP through Linux *unprivileged ping sockets*, so no vakr process needs root or `CAP_NET_RAW`. The kernel allows a process to open one only if one of its groups falls inside the range in `net.ipv4.ping_group_range`. That one setting covers IPv4 and IPv6.

The package creates the `vakr` service group with a system GID below 1000. systemd's default range is `0 2147483647`, which includes it. Many hardened images narrow the range to human users, for example `1000 9999`, and then the collector is refused.

`vakr doctor` (and the `icmp` line of `vakr check health`) checks this for you:

```console
$ vakr doctor
...
[WARNING ] icmp: unprivileged ICMP refused: the vakr service user (GID 987) is outside net.ipv4.ping_group_range 1000 9999; fix: echo 'net.ipv4.ping_group_range = 987 9999' | sudo tee /etc/sysctl.d/99-vakr-icmp.conf && sudo sysctl --system
```

Run the printed fix as shown. It widens the range just enough to include the `vakr` group and never removes access for anyone who has it today. The drop-in is named so that it is read after the usual local files. If another file read later still sets the key, for example `/etc/sysctl.conf` (which `sysctl --system` reads last), the check names that file and line, and you edit it instead. To check by hand:

```console
$ sysctl net.ipv4.ping_group_range
net.ipv4.ping_group_range = 987	9999
$ getent group vakr
vakr:x:987:
```

You may prefer systemd's default, `0 2147483647`, which lets every group use ping sockets. That is a site policy choice; vakr only needs its own group included.

The check reports WARNING rather than CRITICAL. A host setting cannot be fixed by rolling back an update, and `check health` decides whether a self-update is kept. While the setting is wrong, ICMP devices show Unknown, and under the default `icmp_gated` strategy their SNMP is skipped too. Fix it promptly.

The package never changes this sysctl itself, because doing so would silently override a hardening decision someone made on purpose.

In a development checkout (`VAKR_ROOT` set, or no `vakr` account on the host), the check tests your own groups instead and opens a ping socket to prove they work.

## Fallback: `CAP_NET_RAW` for the collector only

Use this only on a host where the range cannot be changed, for example inside a container that cannot set sysctls. It lets the collector open raw ICMP sockets instead. The file is an example, not installed by the package:

```console
$ sudo install -D -m 0644 /path/to/vakr/packaging/examples/vakr-collector.service.d/50-icmp-cap-net-raw.conf \
    /etc/systemd/system/vakr-collector.service.d/50-icmp-cap-net-raw.conf
$ sudo systemctl daemon-reload
$ sudo systemctl restart vakr-collector.service
```

It contains only:

```ini
[Service]
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
```

When unprivileged ping sockets are refused, the collector tries a raw socket. That succeeds only with this drop-in installed. `vakr doctor` then reports `icmp` as OK and names the drop-in. **Never** add this capability to `vakr-api.service` or any other unit: the API, web UI, and maintenance processes must not be able to open raw sockets ([15.3](../../tool-blueprint.md#153-process-privilege-separation)). To remove the fallback, delete the file and run the same two `systemctl` commands.

## `vakr icmp probe`

Run one reachability cycle by hand and see exactly what the collector would see from this host:

```console
$ vakr icmp probe 192.0.2.1
probing 192.0.2.1 (IPv4) from the default source: 5 probes, 200 ms apart, 1000 ms timeout
  probe 1: reply in 1.204 ms
  probe 2: no reply within 1000 ms (lost)
  probe 3: lost: host unreachable (reported by 198.51.100.1)
  probe 4: reply in 1.198 ms
  probe 5: reply in 1.311 ms

state: impaired (loss): 3 of 5 replies, 40.0% loss
  rtt min/avg/median/max: 1.198/1.238/1.204/1.311 ms, jitter 0.052 ms
  SNMP under icmp_gated: would run
```

| Option | Default | Meaning |
|---|---|---|
| `TARGET` | | IP address or host name. A name is resolved once, within 2 s. |
| `--count N` | `icmp.probes` (5) | Probes to send, 1-100. One probe can never show Reachable. |
| `--interval-ms MS` | `icmp.spacing` (200) | Time between probe starts. |
| `--timeout-ms MS` | `icmp.timeout` (1000) | Wait per probe. |
| `--source ADDR` | none | Source address to send from; it must be configured on this host. |

The thresholds come from `/etc/vakr` when it is readable, and from the packaged defaults otherwise. Run the command as a user whose group is inside `ping_group_range`. As root it falls back to a raw socket when ping sockets are refused, so it can succeed where the service fails. To test exactly as the service does, run `sudo -u vakr vakr icmp probe TARGET`.

The exit status follows the Nagios plugin convention, so the command also works as a CheckMK or Nagios check:

| Exit | Meaning |
|---|---|
| 0 | Reachable |
| 1 | Impaired: it answers, with loss or high latency |
| 2 | Unreachable: no replies. Command-line usage errors also exit 2, with a usage message. |
| 3 | Unknown: the collector could not observe the device, or the target is invalid or does not resolve |

A probe the collector could not send is printed as `not sent: <reason>; not counted as loss`, followed by a `collector problem:` line. That is a problem on this host, not the device:

| Reason shown | What to do |
|---|---|
| refuses unprivileged ICMP ... `ping_group_range` | Apply the fix above, or run the command as a user in the range. |
| no ICMP for this address family | IPv6 is disabled on this host. |
| source address is not configured on this host | Fix the `--source` address or the device's pinned source. |
| no route to the target | This host's routing table has no path. Check its routes or VRF. |
| a local firewall rule ... refused it | A local `nftables`/`iptables` rule or a broadcast target. |
| too busy to send it on time | The collector is overloaded; see `icmp.max_in_flight`. |
| receive buffer overflowed | Replies arrived faster than they were read. Raise `net.core.rmem_max` above 1 MiB on busy collectors. |

## Settings

All in the `[icmp]` table; see the [configuration reference](configuration.md#icmp).

| Option | Default | Notes |
|---|---|---|
| `icmp.probes` | 5 | 2-5. At least 2, because `icmp_gated` runs SNMP only after two replies. At most 5, the RTT columns kept per cycle. |
| `icmp.spacing` | `"200ms"` | 20 ms-5 s |
| `icmp.timeout` | `"1s"` | 100 ms-5 s. With the defaults a cycle ends within about 2 s. |
| `icmp.impaired_loss_percent` | 40.0 | Loss at or above this is Impaired. |
| `icmp.impaired_rtt_ms` | 0.0 (off) | Median RTT above this is Impaired. |
| `icmp.max_in_flight` | 1024 | Probes waiting for replies at once across all devices. Needs a restart. |

## How the collector measures

All probes share one socket per address family (two for most collectors), plus one per pinned source address. Replies are matched by sequence number, a random token in the payload, and the replying address. Late, duplicate, and foreign replies are counted and dropped. Probe RTTs use the kernel's arrival timestamp, so a reply read late because the collector was busy keeps its true RTT. Every limit (probes in flight, sockets, the pending-reply table, the per-cycle deadline) is listed in [Appendix E.22](../../tool-blueprint.md#e22-icmp-adapter-adr-023-icmp-001012).
