# Inventory: devices, sites, annotations, and interface identity

vakr keeps the list of devices it polls in its metadata database. You can manage that list from a file under version control, `/etc/vakr/inventory.toml`, and apply it with a plan you review first. This page covers the file format, `vakr inventory validate|plan|apply|list`, how interface annotations in the file take effect (`vakr interface claim`), and what happens to interface identities when devices are rediscovered, including the cases vakr leaves for you to decide (`vakr interface review`).

The rules are in the blueprint, sections [11.1–11.4](../../tool-blueprint.md#11-configuration-model), [8.2](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership), [8.10](../../tool-blueprint.md#810-persistent-interface-identity-requirements), and Appendix [E.26](../../tool-blueprint.md#e26-inventory-and-reconciliation).

## The file

`/etc/vakr/inventory.toml` (moved by `VAKR_ROOT`, or set `inventory.file` to another absolute path) has three kinds of table. Every key is optional except where noted.

```toml
# Sites are optional. A device may also name a site created in the UI.
[[sites]]
name = "dal1"                 # required, unique
code = "DAL1"                 # default: the name in upper case
region = "US-South"
timezone = "America/Chicago"  # IANA name
description = "Dallas primary data center"
# uuid = "..."                # keep this site's identity if you rename it

[[devices]]
name = "edge1.example.net"    # required, unique; the display name
address = "192.0.2.10"        # required, unique: an IP address or a DNS name
site = "dal1"
credential = "network-v2c"    # a NAME in /etc/vakr/secrets/credentials.toml, never the community
tags = ["edge", "wan"]
groups = ["routers"]
comment = "Primary WAN router"
# admin_state = "enabled"     # enabled | maintenance | disabled | retired
# interval = "60s"            # poll interval override, 30s to 1h
# discovery_interval = "24h"  # rediscovery override, 30s to 30d
# reachability = "icmp_gated" # icmp_gated | snmp_fallback | icmp_only | snmp_only
# address_family = "ipv4"     # for a DNS name that resolves to both families
# uuid = "..."                # keep this device's identity if you rename it

[[devices]]
name = "sw1.example.net"
address = "192.0.2.20"
site = "dal1"
credential = "network-v2c"
interval = "120s"

# Annotations describe what an interface is for (META-007, META-011).
[[interface_annotations]]
device = "edge1.example.net"  # required: a [[devices]] entry in this file
ifname = "Gi0/0/0"            # required: ifName as the device reports it
high_priority = true          # same as interest = "primary"
wan_circuit = true
sticky = true                 # list it first on device pages
provider = "Example Carrier"
circuit_id = "CKT-0001"
contracted_bps = 100000000
# capacity_override_bps = 1000000000
# display_rank = 10
# tags = ["transit"]
# comment = "Handed off on port 3 of the carrier NID"
# public_dashboard_eligible = false
```

Names and values are trimmed, so a stray trailing space does not create a different device or credential. IP addresses are compared in canonical form. The file may hold up to 1,000 sites, 5,000 devices, and 20,000 annotations, and at most 4 MiB.

Credentials stay in the protected credentials file ([credentials guide](credentials.md)). The inventory names them; vakr checks that each name exists without reading any secret.

## Checking and applying the file

| Command | What it does | Changes anything? |
|---|---|---|
| `vakr inventory validate` | Checks the file, and its conflicts with what is already stored | No |
| `vakr inventory plan` | Shows exactly what `apply` would do | No |
| `vakr inventory apply` | Makes those changes in one transaction and records who did it | Yes |
| `vakr inventory list` | Lists stored devices, their state, and who manages them | No |

`validate`, `plan`, and `apply` accept `--config-dir DIR`, `--file FILE`, and `--json`. Exit status: **0** success, **1** the file has problems or conflicts with stored records, **2** usage error, **4** the metadata database is unavailable (`apply` and `list` need it; run `vakr db migrate` on a new install, and see `vakr db status` otherwise).

A plan lists each change on one line, in a fixed order, so you can review it like a diff:

```text
$ sudo vakr inventory plan
inventory.toml: 1 create, 1 update, 1 disable, 3 unchanged
  create device core2.example.net: 192.0.2.30 at site dal1
  update device sw1.example.net: interval_s: 60 -> 120
  disable device old-sw.example.net: no longer in inventory.toml; polling stops, history is kept
waiting (1 annotation(s) not applied yet):
  edge1.example.net Gi0/0/0: API-managed; run `vakr interface claim edge1.example.net Gi0/0/0` to let inventory.toml govern it (E.19)
```

Applying the same file twice changes nothing the second time.

### Problems are reported where they are

Every problem names the file and the place in it, and one run lists them all:

```text
inventory.toml: devices[1].address: 169.254.169.254 is denied by security.denied_target_addresses (169.254.169.254/32) (15.5)
inventory.toml: devices[2].name: device name already used by devices[0]
inventory.toml: devices[3].credential: no SNMP credential named 'netwrok-v2c' in the credentials file
inventory.toml: devices[4].name: device 0190c2… is api:api; remove the entry, or remove or retire the API-managed record first (CONF-008)
```

`vakr config validate` also checks the installed `inventory.toml` when it exists (everything except conflicts with stored records, which `vakr inventory validate` adds).

### Who owns a record

Every device and site the file creates is marked `file:inventory.toml`. The web UI and API cannot change those records; edit the file instead. Records created in the UI stay UI-managed, and the file cannot take them over: an entry with the same name, UUID, or address as a UI-managed device is an error, never a silent overwrite (CONF-008). Remove the entry from the file, or remove or retire the UI record first.

**Removing a device from the file disables it.** It stops being polled and keeps all of its history; nothing is deleted. Put it back and it is enabled again. `apply` refuses to run when the file does not exist, so a mistyped path cannot disable everything; to disable every file-managed device on purpose, apply an empty file.

**Renaming.** A device is matched by name. To rename one and keep its graphs, give it a `uuid` (shown by `vakr inventory list --json`) before you change the name.

### Target guardrails

Polling sends packets wherever the inventory says, so every IP address is checked when you validate ([15.5](../../tool-blueprint.md#155-network-target-guardrails)):

- multicast, unspecified (`0.0.0.0`, `::`), and broadcast addresses are always refused;
- addresses in `security.denied_target_addresses` are refused. The default denies the cloud metadata services `169.254.169.254`, `fd00:ec2::254`, and `100.100.100.200`;
- if `security.allowed_target_cidrs` lists networks, every target must be in one of them. Empty, the default, allows any unicast address.

A DNS name is checked when the collector resolves it, because its address can change.

## Interface annotations and `vakr interface claim`

Interfaces come from discovery, so they exist only after a device has been polled for the first time, and they start out managed by the UI. An annotation in the file takes effect once you **claim** the interface for the file:

```text
$ sudo vakr interface claim edge1.example.net Gi0/0/0
claimed edge1.example.net Gi0/0/0 (0190c2…) for inventory.toml
  interest: secondary -> primary
  is_wan_circuit: False -> True
  provider: none -> Example Carrier
```

The claim moves the interface to `file:inventory.toml`, writes the annotation, and records an audit event naming you. From then on `vakr inventory apply` keeps it in line with the file. Until you claim it, `vakr inventory plan` lists the annotation under "waiting" with the command to run. Other reasons an annotation waits:

- **not discovered yet**: the device has not been polled since it was added;
- **two interfaces are named …**: vakr is not sure which interface is which (see below); resolve the review first.

If you remove an annotation from the file, the next apply resets that interface's annotation and hands it back to the UI.

## Interface identity after rediscovery

Every interface has a permanent identity (a UUID), and its graphs and history belong to that identity, not to its `ifIndex`. A router reload can renumber every `ifIndex`; vakr follows the interface by name. When a device is rediscovered (every 24 hours by default), each interface the device reports is matched to a known identity:

| What vakr sees | What it does |
|---|---|
| Same `ifIndex`, same name | Same identity (**exact**). A changed description never splits an identity. |
| A name that exists once on the device moved to an `ifIndex` nothing else had | Same identity, new `ifIndex` (**confident renumber**); the old index is kept in its history. An interface that went missing and came back is reconnected the same way. |
| Nothing known shares its name, `ifIndex`, MAC, or addresses | A **new** identity |
| A known interface is gone (and the device's interface table was read completely) | That identity becomes inactive and keeps its history (**missing**). After ten days inactive it is **stale** and is checked only at rediscovery. |
| Anything less certain: a different name at the same `ifIndex`, two ports that traded numbers, a name reported twice | **Waits for you** (probable or ambiguous) |

vakr never guesses in the last case, because merging the wrong histories would put one circuit's traffic on another circuit's graph. Until you decide, the interface is still polled for status but **neither the old nor the new identity receives data**, and samples are marked as having no resolved identity. MAC addresses only count as evidence when they are unique on the device; IOS XE gives SVIs, subinterfaces, and port-channels the same MAC.

### Deciding: `vakr interface review`

```text
$ sudo vakr interface review list
review 0190c3…: edge1.example.net ifIndex 5 'Te0/0/5' [probable] since 2026-10-21 03:00Z
  candidate Gi0/0/5 (ifIndex 5, active) 0190c2…
```

- `vakr interface review map REVIEW INTERFACE` says the row **is** that interface: it keeps its UUID and history and takes the new name and `ifIndex`.
- `vakr interface review new REVIEW` says the row is a **different** interface: it gets a new identity, and the old one becomes inactive with its history kept.

Each decision is recorded in the interface's identity history with your login and in the audit log. The next rediscovery matches the interface exactly. If a device shows several cases at once (for example after a line-card swap), resolve them all: an identity that is a candidate in any open case receives no data.

The rules are deliberately cautious for the MVP. Their weights will be tuned from field captures of IOS XE devices (ADR-015), which should reduce how often you are asked.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `metadata database unavailable … does not exist` | New install: run `sudo vakr db migrate`. |
| `credential names not checked: …` | The credentials file is missing or unreadable for the user running the command; run it with `sudo`, or see the [credentials guide](credentials.md). |
| An annotation stays under "waiting" | Read the reason on that line; usually the device has not been discovered yet, or the interface needs `vakr interface claim`. |
| An interface has no graphs after a reload | Look for an open case with `vakr interface review list`. |
