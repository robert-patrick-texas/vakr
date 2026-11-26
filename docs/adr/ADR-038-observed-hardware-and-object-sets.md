# ADR-038: Observed hardware, the model catalog, device form, and object sets by model and form

| Field | Value |
|---|---|
| Status | Proposed 2026-11-26. The direction is the owner's (see Context). The details below are proposals for the owner to review. **When:** after the 14-day parallel run of MVP acceptance, so that the run tests what was reviewed; ADR-037 goes before the production install (the owner, 2026-11-26) |
| Date | 2026-11-26 |
| Decides | What vakr keeps of the hardware a device reports; how vakr recognizes a model from hirdir's catalog; how a stack or a cluster is one device or several; how an operator adds SNMP objects for a model or a form, so that a switch stack is polled for more than a router |
| Requirements | Blueprint 2.4, 7.1 (Group type), 8.3 (DISC-004, DISC-008, DISC-009), 8.4 (POLICY-001 to POLICY-007), 9.4, 22, Appendix E.18, E.23, E.26; META-001 |
| Related | [ADR-037](ADR-037-inventory-from-hirdir.md); hirdir's [Integrations](https://github.com/robert-patrick-texas/hirdir/blob/main/docs/integrations.md) page, P-336 and P-337; sunstone's SI-04 and SI-06 |

## Context

The owner, on 2026-11-26: "Yes, vakr should store serials and models and should include
options to allow an operator to add additional OIDs by device model type (shared catalog
entries from hirdir?) and stacking identifiers to identify when a device should be polled
for additional information compared to another device (e.g., Cisco switch stack has
additional MIB values to check versus a Cisco router). Integrate catalog from hirdir with
both vakr and sunstone to ensure device type remains matched across each project, and how
to show a stack or cluster from hirdir in the other projects?"

Where vakr stands today:
- **Discovery reads the system group, but does not keep it.** It reads `sysDescr`,
  `sysObjectID`, `sysName`, `sysContact` and `sysLocation` (DISC-004), but they are "not
  kept in inventory yet".
- **No hardware table.** vakr reads no ENTITY-MIB, and has no serial number, model or
  member of a stack.
- **A fixed set of objects.** Every device is polled for the objects of §9.4.
- **Groups have no type.** A group is a list of devices, and the group type of §7.1 is
  not built.

Section 2.4 says vakr is not a general-purpose CMDB.

## Decision

### 1. vakr keeps the hardware it sees

- **What discovery keeps.** The system group values it already reads (DISC-004). For
  each entry of ENTITY-MIB's `entPhysicalTable` (`1.3.6.1.2.1.47.1.1.1`) of the class
  chassis(3) or stack(11), it keeps:
  - `entPhysicalDescr`, `entPhysicalName`, `entPhysicalModelName`,
    `entPhysicalSerialNum`, `entPhysicalSoftwareRev` and `entPhysicalHardwareRev`;
  - the parent (`entPhysicalContainedIn`) and the position within it
    (`entPhysicalParentRelPos`).
- **How it is stored.** The current entries are kept in a `device_hardware` table. A
  change is appended to `hardware_history`, which records the source and the time, as
  `identity_history` does.
- **What is missing.** A device with no ENTITY-MIB has a `null` value, with a reason.
  vakr never writes an empty string in its place (DISC-008).
- **How it is read.** `GET /api/v1/devices/{uuid}/hardware` returns the entries. The
  device lists and exports gain `observed_vendor`, `observed_pid`,
  `observed_serial_number` and `observed_catalog_entry_hid`, all read from the chassis,
  or from the active member of a stack.
- **What it is.** These are observations. Nothing in vakr edits them, and nothing in
  vakr's inventory changes because of them.

### 2. A copy of hirdir's catalog

- **The file.** `/etc/vakr/catalog.toml` is written by `vakr inventory pull` (ADR-037)
  and managed by the source. It may also be written by hand where there is no hirdir. It
  holds:
  - `[[vendors]]`: `hid`, `name`, `aliases` and `enterprise_number`;
  - `[[models]]`: `catalog_entry_hid`, `vendor`, `pid`, `pid_aliases`, `model_name`,
    `kind`, `type`, `function` and `sys_object_ids`.
- **Recognizing a model.** The chassis's `entPhysicalModelName` is looked up as a PID,
  or an alias of one, of the vendor. Failing that, the `sysObjectID` is looked up among
  the models' `sys_object_ids`. The vendor is the one whose `enterprise_number` follows
  `1.3.6.1.4.1.` in the `sysObjectID`.
- **Two models, compared.** A device carries hirdir's `catalog_entry_hid` (what hirdir
  says) and `observed_catalog_entry_hid` (what vakr recognized). A difference is shown
  on the device and reported to hirdir (hirdir P-336). It changes nothing.
- **An unknown model** is shown, and reported, as the vendor and PID that were seen.
- **The lists of types and functions** are hirdir's, a site may change them, and vakr
  does not hard-code them.

### 3. A device's form: a stack is one device, a cluster one or several

- **The declared form.** A device has `form`, from hirdir: `single`, `stack`,
  `cluster_member` or `cluster`. A stack also has `expected_members`.
- **The observed form.** `observed_form` comes from discovery. A stack(11) entry, or more
  than one chassis(3), is a stack. A difference from `form` is shown.
- **A stack's members are entries within one device**, never devices of their own. vakr
  reports the members to hirdir, each with its switch number, serial number, model and
  role, and the active switch as the observed master (hirdir P-336). On Cisco, an
  interface whose first number is the member's switch number belongs to that member.
- **How a cluster is polled depends on how hirdir manages it:**

| hirdir's `managed_as` | vakr |
|---|---|
| `one` | One device at the cluster's address, with the group's `hid`; `form` `cluster` |
| `members` | Each member is a device with its item's `hid` and `form` `cluster_member`. The members are gathered in a group of the `type` `cluster` that carries the cluster's `hid` |
| `members_and_cluster` | Both: a device for each member, and one more for the cluster's own address |

- **Typed groups.** The group `type` of §7.1 is built for this: `cluster`, and `custom`
  for the rest.
- **A cluster's status** is degraded while one of its members is down, and failed when
  all of them are.

### 4. Object sets, matched by model and form

**What a set holds.** An object set is a named list of SNMP objects. For each object it
gives:
- `name` and `oid`;
- `table`, true for a column of a table;
- `syntax`: `text`, `gauge` or `counter`;
- `unit`.

It may also name, in `requires`, an object that must answer before the set is polled.

**Which devices take a set.** Each set has a `match`, written in the collection policies'
pattern syntax (POLICY-005). It is matched on the facts policies already know (site,
tags, groups) and on new ones:
- `vendor` and `enterprise_number`;
- `pid` and `catalog_entry_hid`;
- the model's `kind`, `type` and `function`;
- `form` and `observed_form`;
- `sys_object_id`.

The sets that match a device add up. A policy rule may leave a set out for some devices
with `object_sets_exclude`. `vakr policy explain` shows which sets a device takes, and
why (POLICY-004, POLICY-006).

**Where sets come from.** The packaged sets are in `/opt/vakr/share/defaults/objects/`:
- `entity-physical`, for every device (decision 1);
- `cisco-stackwise`, for `form` or `observed_form` `stack`, which requires
  CISCO-STACKWISE-MIB's switch table. It reads each switch's number, role, state and
  MAC address, and the operational state of each stack port.

An operator's own sets go in `/etc/vakr/objects.toml`.

**The router and the stack.** A Cisco router matches neither the form `stack` nor the
required table, so it is polled for less than a Cisco switch stack.

```toml
# /etc/vakr/objects.toml: an operator's own set (illustrative)
[[object_sets]]
name = "c9300-revisions"
class = "inventory"
match = { vendor = "Cisco", pid = "C9300-*" }
objects = [
  { name = "hardware_rev", oid = "1.3.6.1.2.1.47.1.1.1.1.8", table = true, syntax = "text" },
  { name = "firmware_rev", oid = "1.3.6.1.2.1.47.1.1.1.1.9", table = true, syntax = "text" },
]
```

**Two classes of set:**
- **`inventory`** objects are read at the discovery interval (DISC-002). They are kept as
  their current value, with a history of changes, and shown on the device.
- **`metric`** objects are read at the poll interval as time series, with rollups and
  graphs. They need a new observation entity and an amendment to ADR-021, and come in a
  later phase.

**Limits** on objects per set, sets per device, rows per table object and value length
go into Appendix E when the sets are built. None is invented here. Capability knowledge
(DISC-009) stops a set from asking again for objects a device does not answer.

**Later: LLDP neighbours.** An `lldp-neighbours` set (LLDP-MIB `lldpRemTable`) could
become sunstone's discovery source and the basis of a later cabling in hirdir. Drawing
the topology stays sunstone's (§2.4).

### 5. Scope

This ADR clarifies the §2.4 non-goal "a general-purpose CMDB". vakr collects and shows
what a device reports about its hardware. What the team intends stays in hirdir. vakr
has no way to edit hardware facts, and keeps none that a device does not report.

### 6. Phases

1. Decisions 1 to 3, with the packaged sets as inventory.
2. Operators' own inventory sets.
3. Metric sets.

## Alternatives

- **The objects to poll kept in hirdir's catalog** (the owner's question, "shared catalog
  entries from hirdir?"). Rejected for the objects themselves, accepted for the catalog.
  - The catalog is shared: hirdir says which model a device is, and vakr matches its
    sets on that.
  - The sets stay vakr's. They are collection settings, with vakr's own meanings for
    syntax, interval, storage and limits, and they change with vakr's collectors.
  - In hirdir, the inventory staff could change what vakr polls without vakr checking
    it.
- **Match sets only on what hirdir declares.** Rejected. A stack that hirdir has not yet
  recorded as one would go unpolled. `requires` lets the device say so itself.
- **Stack members as devices.** Rejected. A stack has one management plane and one
  address, and its members have no SNMP agent of their own.
- **A MIB compiler, so objects are named instead of numbered.** Deferred. Numeric OIDs
  keep the sets plain, and a name is a comment.

## Rationale

The model and the form of a device decide which objects are worth reading. The model is
the inventory's to say, and the form is both the inventory's (intended) and the device's
(observed). Matching sets on facts that already flow from hirdir keeps one catalog across
the three projects, and leaves polling decisions where the polling is done.

## Benefits

- Serial numbers and models seen on the wire let hirdir find swaps nobody recorded,
  members of a stack moved between stacks, and models it lacks.
- A stack shows its members and its ring without being several devices.
- Operators add objects for a model without a code change.
- The same `catalog_entry_hid` and type mean the same in vakr, hirdir and sunstone.

## Risks

- **Load on weak SNMP agents.** Extra objects add load to agents that are already weak.
  Sets are bounded (Appendix E), inventory sets run at the discovery interval, and the
  per-device concurrency of one PDU stays.
- **Inconsistent vendor data.** Vendors fill ENTITY-MIB unevenly. Missing values are
  `null` with a reason, and a model that cannot be recognized is reported, never guessed.
- **Too many sets.** A device may take more sets than intended. `explain` shows why.

## Security impact

Operators' sets are configuration. They are validated when they load: OIDs in dotted
decimal, and patterns checked as policies' patterns are (E.18). No set can write to a
device: vakr sends GETs and walks only.

## Performance impact

At the MVP's scale, ENTITY-MIB's physical table is tens of rows a device, read once a
discovery interval. Stackwise tables are a few rows for each member. Metric sets come
with their own measurement before they are accepted.

## Compatibility and migration impact

New tables (`device_hardware`, `hardware_history`, the catalog, typed groups) arrive by
migration. No existing field changes. `objects.toml` and `catalog.toml` are new files,
whose defaults come from `/opt/vakr/share/defaults/` (§28.4).

## Test changes

- Simulator fixtures (ADR-034, §23):
  - a stack with three members, and one with a member missing;
  - a router with no stack table;
  - a device with no ENTITY-MIB.
- Recognizing a model by PID, by alias and by `sysObjectID`.
- `form` against `observed_form`.
- Matching sets, and their union.
- `requires`.
- The limits.
- The report rows that go to hirdir.

## Documentation changes

- `docs/operations/snmp.md`: object sets and the catalog.
- `docs/operations/inventory.md`: form and hardware.
- Blueprint §7.1, §8.3, §8.4, §9.4 and Appendix E, once accepted.
- `docs/traceability.md`.

## Rollback and revisit criteria

- Removing `objects.toml` and the packaged sets returns polling to §9.4. The hardware
  tables can stay as history.
- Revisit:
  - if the load measured on real stacks is too high at the discovery interval;
  - if hirdir's catalog cannot express a model's identity well enough to recognize it.
