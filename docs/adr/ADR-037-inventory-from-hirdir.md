# ADR-037: Inventory from hirdir: common keys, shared fields, and circuits

| Field | Value |
|---|---|
| Status | Proposed 2026-11-26. The owner set the direction: hirdir, vakr and sunstone move toward common keys for a device, a site and an operator, and share circuit IDs in all their forms, with the same fields holding the same content in each project. The details below are proposals for the owner to review. **When:** before vakr's production install, so that production starts on the shared names and no production data is migrated for them (the owner, 2026-11-26: "a, renames before the production install") |
| Date | 2026-11-26 |
| Decides | Which keys vakr carries from hirdir; which of vakr's fields take hirdir's names; how a WAN circuit is described; how vakr takes its inventory from hirdir and reports back to it; what other tools read vakr with |
| Requirements | Blueprint 1.2 (source of configuration truth), 2.4, 3.2, 3.3, 7.1, 8.2 (META-001, META-006 to META-010), 8.17 (API-003), 11.2, 14, 15.1, 22, Appendix E.26, E.30, E.37, E.38; SEC-008, API-T08 |
| Related | hirdir's [Integrations](https://github.com/robert-patrick-texas/hirdir/blob/main/docs/integrations.md) page and its P-329 to P-339; sunstone's SI-01 to SI-10; [ADR-038](ADR-038-observed-hardware-and-object-sets.md) |

## Context

hirdir is the owner's inventory. It holds regions, sites and locations; devices, stacks
and clusters; the catalog of models; operators and contacts; and circuits. sunstone is
the owner's network map. vakr holds a thin copy of some of those facts:
- sites, with no coordinates and no hierarchy;
- devices, by a unique `name`;
- WAN circuits, as free text on one interface: `provider`, `circuit_id`,
  `contracted_bps`.

Today all three are typed by hand into `inventory.toml`, into a CSV or TOML import, or
through the admin API.

The owner, on 2026-11-26: "The goal is to shape these projects to move toward one
another to enable integration and move to common approaches where it makes sense,
ultimately toward a common reference keys for a device, a site, an operator. Circuit ID
in all its forms should be shared across each project, carrier/provider circuit ID,
enterprise circuit ID, same fields with same content across the tools/projects."

Three facts from the code shape this ADR:
- **No external reference.** No vakr record has a field for an outside key. A
  caller-supplied `uuid` in `inventory.toml` and in an import is the nearest thing.
- **No machine reader.** The only machine credentials are kiosk tokens (`vkk_`), which
  have the operator role and read everything, and replication tokens (`vkr_`). The
  integration reader of §3.2 and API-003 is not built.
- **Lists change order while being read.** Device and interface lists are ordered by
  live status (E.30), so a row can move between pages while a reader walks them.

## Decision

### 1. Keys

- **Sites and devices carry their HID.** A site and a device carry `hid`: the HID of the
  record in hirdir, a lower-case UUIDv7. It is read-only, and set only by the inventory
  source.
- **References carry the other record's HID.** References to hirdir records are named
  `<name>_hid`: `site_hid`, `operator_hid`, `circuit_hid`, `carrier_hid`, and
  `catalog_entry_hid` (ADR-038).
- **A device's `hid` names the managed node.** That is a hirdir item that stands alone,
  or a stack or cluster managed as one device, named by the group's HID.
- **`hid` is a reference, not identity.** META-001 is unchanged: vakr's own `id` is the
  device's identity, and its history hangs on it. When hirdir records a replacement of a
  device that stands alone, the next pull gives the vakr device the new item's `hid`.
  The device and its graphs stay as they are.
- **`hid` is unique and can be looked up.** It is unique among devices that are not
  retired. `GET /api/v1/devices?hid=` finds a device by it, and `device-lookup?key=`
  accepts it beside the name and the UUID.

### 2. Shared fields under hirdir's names

A field that vakr and hirdir hold for the same fact takes hirdir's name, in vakr's
model, files and API. vakr's own fields keep their names: `address`, `credential`,
`admin_state`, intervals, `interest`, `sticky`, `capacity_override_bps`.

| Record | Now | Proposed |
|---|---|---|
| Site | `timezone` | `time_zone`, an IANA name, from hirdir's `sites.time_zone` |
| Site | `region` (free text) | `region`: hirdir's name for the site's region |
| Device | `site` (by name) | `site_hid`. A hand-written file may still name the site |
| Device | (none) | `hostname`, `fqdn` from hirdir, shown beside `name`, which is hirdir's name in force |
| Device | (none) | `operator_hid` and `operator` (the operator's label). Device lists filter by them, and rankings group by them |
| Device, site, circuit | (none) | a link to the record in hirdir, from a template in the configuration that holds `{hid}` |

vakr holds no owners, contacts or people. A page that needs them links to hirdir.

### 3. A circuit's fields, the same as hirdir's and sunstone's

An interface's circuit annotation becomes hirdir's block of circuit fields:

| Field | Content | Replaces |
|---|---|---|
| `circuit_hid` | the circuit's HID in hirdir | (none) |
| `enterprise_circuit_id` | the enterprise's own ID, one value for the whole circuit | (none) |
| `circuit_type` | from hirdir's list: `internet`, `mpls`, `point_to_point`, `dark_fiber`, `wavelength`, `voice`, `other` | (none) |
| `carrier`, `carrier_hid` | the primary carrier: a vendor's name and HID | `provider` |
| `carrier_circuit_id` | the ID the primary carrier gives the circuit, as the carrier writes it | `circuit_id` |
| `carriers` | the other carriers, each with `carrier`, `carrier_hid`, `role`, `side` and `carrier_circuit_id` | (none) |
| `port_mbps`, `down_mbps`, `up_mbps` | the port's speed, and the download and upload rates the circuit is limited to, in Mbps. A blank rate means the port's speed | `contracted_bps` |
| `side` | `a` or `z`: the side of the circuit this interface's device is on | (none) |

`is_wan_circuit` stays. `capacity_override_bps` stays, as vakr's own override of the rate
it computes utilization against.

**Which rate each direction uses.** `down_mbps` is the rate toward the A side. On an
interface whose `side` is `a`, inbound traffic is compared with `down_mbps` and outbound
traffic with `up_mbps`. On the Z side it is the other way round.

**Reading circuits.** `GET /api/v1/wan-circuits` returns the whole block. The filter
`provider` becomes `carrier`, and the device and interface lists also filter by
`enterprise_circuit_id` and `circuit_hid`.

### 4. Inventory from hirdir, and what vakr reports back

**Configuration.** `[inventory.source]` names `kind = "hirdir"`, the API's `url`, a
credential reference for a token of a hirdir service account with View-only, and
`apply = "review"` (the default) or `"auto"`.

**What vakr polls is decided in hirdir.** It is the records registered in hirdir with
vakr's external system, as `pending` (asked for) or `registered` (confirmed). A person
asks by ticking vakr's box on the item's or the stack's page in hirdir (hirdir P-341). A
registration that is `removed` disables the device. Nothing is deleted (META-009).

**`vakr inventory pull`** reads those records from hirdir's API, and with each one:
- its site and region;
- its primary management address, or its FQDN;
- its model (ADR-038);
- its operator;
- the circuits it is connected to, with the interface and side of each.

**What the pull writes.**
- It writes a file with the source's name, managed by that source:
  `managed_by_kind = "file"`, `managed_by_source = "source:hirdir"`.
- `vakr inventory plan` shows what would change, and `apply` applies it, as for any
  inventory file.
- Pages show these records as managed in hirdir, with the link from decision 2, and
  refuse to change them (`409 managed_by_file`, META-008).
- If hirdir cannot be reached, the inventory last applied stays as it is, and the pull
  says why it failed.
- Two hirdir records that would give two devices the same `name` are named in the plan,
  and neither is applied until one is renamed in hirdir.

**Annotations from the source.**
- An interface annotation from the source takes effect without the per-interface
  `vakr interface claim` of E.26, which was meant for hand-written files.
- An annotation that an operator set through the API is a conflict. The plan names it
  and does not overwrite it, as §22 says.

**`vakr inventory report`** sends hirdir what vakr sees:
- It writes two JSON files under the shared names: one of items, and one of device
  groups for stacks and clusters.
- It sends them to hirdir's import API with `system` set to vakr's external system, the
  mode `update`, and `complete`, then applies them.
- It uses a token of a hirdir service account with the Inventory role.
- Each row gives `hid`, `external_id` (vakr's `id`), and, where vakr has a fresh reading,
  `observed_status` and `observed_at`. ADR-038 adds the observed hardware.

| vakr sees | `observed_status` |
|---|---|
| reachable from every position | `online` |
| reachable from some positions only, or with loss or latency over its threshold | `degraded` |
| unreachable from every position, with fresh readings | `unreachable` |
| stale or never polled, or `admin_state` `maintenance` | not given: the row only confirms the registration, and hirdir's value stays |

A device with no fresh reading still has its row, so that `complete` does not mark it
as no longer reported. Missing data is never sent as down.

### 5. Access for other tools

- **The integration reader (§3.2, API-003)** comes first: a bearer token that can read
  inventory, current state, history, WAN circuits and exports, and can change nothing
  (API-T08). It is apart from kiosk tokens. The database roles of SEC-008 follow with
  PostgreSQL. Until the reader is built, sunstone reads with a kiosk token.
- **Stable paging.** The lists take `sort=id`, an order by vakr's `id` that does not
  change while a reader walks the pages.
- **An events feed.** `/api/v1/events` (§14) is moved ahead for sunstone: device state
  changes from each position, and interface operational changes.

### 6. One rule for an interface's name

Files, imports and the source match `(device, ifname)` by the canonical form of hirdir's
P-331. It takes out spaces, uses lower case, and writes the short form of a type out in
full from one table, with no prefix guessing. The stored `if_name` stays as the device
reports it. Identity reconciliation (§8.10) is unchanged. The table and its test vectors
are on hirdir's Integrations page, and vakr's tests use the same vectors.

### 7. What does not change

- Credentials stay vakr's.
- Discovery still creates no devices.
- vakr holds no intended hardware facts: ADR-038 keeps what is seen apart from what
  hirdir says.
- Hand-written inventory without a source keeps working as it does today.

## Alternatives

- **hirdir writes vakr's file.** Rejected. hirdir adds no outbound hooks before 1.0.0,
  and its later sync works in batches from its audit trail (hirdir P-223). A reader that
  pulls from the owner keeps each project's adapter in that project.
- **vakr's device UUID set to hirdir's HID.** Rejected. A hirdir item is hardware, and a
  replacement makes a new item, while vakr's device is the node and keeps its history.
  A stack's HID would fit, but a device that stands alone would not.
- **A neutral `source_ref` in place of `hid`.** Rejected by the owner's direction toward
  common keys. `hid` says what it is.
- **Keep vakr's names and map them in a connector.** Rejected. The owner asked for the
  same fields with the same content in every project, so a file from one project loads
  in another with little translation.
- **A hirdir filter in place of registrations to choose what vakr polls.** Possible, but
  a registration also records, in hirdir, who asked for monitoring and when vakr
  confirmed it.

## Rationale

The keys and names that the three projects share make every later integration a matter
of moving rows, not translating them. hirdir already keeps registrations, observations
apart from intent, an import of what a tool reports, and a mode that changes only what
is there (hirdir P-248, P-334). vakr already keeps records managed by a file and refuses
API writes to them. Those two halves meet with little new machinery.

## Benefits

- A device, site, operator or circuit is the same record in every tool, through renames,
  moves and replacements.
- hirdir shows which devices vakr watches, what it sees, and what nobody watches.
- Circuit rates in hirdir set utilization in vakr, both ways, for asymmetric circuits.
- Operators are typed once, in hirdir.

## Risks

- **hirdir becomes a dependency of vakr's inventory.** The last applied inventory stays
  when hirdir is down, and a pull is never needed for collection.
- **API churn on both sides** (vakr's ADR-031, hirdir before 1.0.0). Contract tests run
  against each project's OpenAPI document.
- **Renaming fields breaks hand-written files and scripts.** Config and data migrations
  carry them over (ADR-031 still requires migrations), and the changelog names each one.
- **Registration-driven polling needs discipline in hirdir.** The first report from vakr
  registers the devices it already polls (hirdir's matching by host name), so nothing is
  lost at the start.

## Security impact

- The hirdir tokens are credential references in `/etc/vakr/secrets/credentials.toml`,
  and are never logged (§15.2).
- The pull's account in hirdir is View-only. The report's account has the Inventory role,
  but its imports are in the mode `update` and so create nothing.
- Outbound HTTPS follows ADR-032, through the proxy and trusted CAs.
- The integration reader opens a read-only door into vakr that does not exist today. It
  is a separate role with its own tokens, which can be revoked.

## Performance impact

A pull of the MVP's 100 devices is a few paged requests. At 5,000 devices it is bounded
by hirdir's list limit of 1,000 rows a page. A report is two imports a run, at the
discovery interval or slower. Neither is on the collection path.

## Compatibility and migration impact

- A config migration renames `timezone` to `time_zone` (§28.4).
- A data migration moves `provider` to `carrier`, `circuit_id` to
  `carrier_circuit_id`, and `contracted_bps` to `down_mbps` and `up_mbps` (bps
  divided by 1,000,000), and adds the new columns empty.
- API fields and filters change name. ADR-031 allows this before enterprise readiness,
  and the changelog marks each change.

## Test changes

- Contract tests of the pull and the report against hirdir's OpenAPI document.
- Plan tests: a conflict with an API-managed annotation, two records with one name,
  a removed registration.
- The interface-name vectors.
- The status mapping, including the stale row.
- API-T08 for the integration reader.
- Stable paging with `sort=id` while statuses change.

## Documentation changes

- `docs/operations/inventory.md`: the source, pull, plan and report.
- `docs/operations/api.md`: the reader, the circuit block, `sort=id`.
- Blueprint §7.1, §8.2 (META-007 and META-010), §11.2 and Appendix E.26, once accepted.
- `docs/traceability.md` for the new requirement IDs.

## Rollback and revisit criteria

- With `[inventory.source]` removed, the generated file can be adopted as a hand-written
  one (`vakr inventory adopt`), and vakr works alone again.
- Revisit if hirdir 1.0.0 changes its import contract, or if sunstone needs a field vakr
  does not have.
