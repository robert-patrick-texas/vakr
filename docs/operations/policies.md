# Collection policies and interface classification

vakr decides three things for every interface it discovers:

- **Is it graphed?** Performance collection is on (monitored) or off (suppressed). A suppressed interface stays in the inventory, keeps its history, and is still rediscovered. Only collection stops ([DISC-007](../../tool-blueprint.md#83-discovery-requirements)).
- **How closely do you look at it?** Interest is `primary` (uplinks, ISP and WAN circuits, the interfaces you open a device page for) or `secondary` (everything else, the default). Primary interfaces, and those marked `sticky`, are listed first ([META-011](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership), [LIFE-007](../../tool-blueprint.md#85-interface-lifecycle-requirements)). Both classes are collected and kept in exactly the same way.
- **Which settings apply?** Poll interval, SNMP timeout, and the other settings in [Settings and their limits](#settings-and-their-limits).

Two inputs drive those decisions:

1. **Description keywords.** Most of the work is already done in your device configs. A description (`ifAlias`) such as `ISP: Carrier-X 1G ckt ABC12345` or `UPLINK to core-sw1 Te1/0/1` tells vakr the interface matters ([META-012](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership)).
2. **Policy rules** in `/etc/vakr/policies.toml`. These suppress noise (loopbacks, SVIs, user ports) and tune settings for groups, tags, sites, devices, or single interfaces ([POLICY-001..007](../../tool-blueprint.md#84-collection-policy-requirements)).

`vakr policy explain` shows, for any interface, every rule considered and which source decided each value. Use it whenever an interface is, or isn't, graphed and you want to know why.

## Classification from descriptions

### How descriptions are read

vakr splits a description into **tokens** wherever a character is not a letter or digit: spaces, punctuation, `-`, `_`, `/`, `:`, and brackets all separate tokens. A keyword matches a **whole token**, ignoring case.

| Description (`ifAlias`) | Tokens | Default keywords matched |
|---|---|---|
| `ISP: Carrier-X 1G ckt ABC12345` | `ISP` `Carrier` `X` `1G` `ckt` `ABC12345` | ISP |
| `[WAN] MPLS primary` | `WAN` `MPLS` `primary` | WAN |
| `UPLINK to core-sw1 Te1/0/1` | `UPLINK` `to` `core` `sw1` `Te1` `0` `1` | UPLINK |
| `WAN-ISP_Carrier` | `WAN` `ISP` `Carrier` | WAN, ISP |
| `uplink to core` | `uplink` `to` `core` | UPLINK (case is ignored) |
| `SWANSEA office` | `SWANSEA` `office` | none: `WAN` is not a whole token |
| `Uplinks to core` | `Uplinks` `to` `core` | none: `Uplinks` is not `UPLINK` |
| `user port`, or an empty description | | none |

Once discovery is running (milestone M3), descriptions are re-read every `discovery.alias_refresh_interval` (1 hour by default), so an edited description on the device reaches vakr within the hour without a full rediscovery ([DISC-011](../../tool-blueprint.md#83-discovery-requirements)). Tags that come from descriptions follow the description: remove `WAN` from it and the `wan` tag goes away.

### The packaged keywords

These ship with vakr ([Appendix E.15](../../tool-blueprint.md#e15-interface-description-keywords-meta-012)):

| Keyword | Tag | Interest | WAN circuit |
|---|---|---|---|
| `ISP` | `isp` | primary | yes |
| `WAN` | `wan` | primary | yes |
| `UPLINK` | `uplink` | primary | no |

### Adding or replacing keywords

Add keywords in `/etc/vakr/config.toml` (or any `conf.d/*.toml` file). They extend the packaged list:

```toml
[[interface_classification.keywords]]
keyword = "TRANSIT"
tag = "transit"
interest = "primary"

[[interface_classification.keywords]]
keyword = "re:CKT[0-9]+"    # a token such as CKT0042
tag = "circuit"
wan_circuit = true
```

Each keyword has:

| Key | Required | Meaning |
|---|---|---|
| `keyword` | yes | One word of letters and digits, matched ignoring case. `exact:DIA` matches the token `DIA` but not `dia`. `re:` is a regular expression that must match one whole token, under the [pattern limits](#pattern-syntax-and-limits). |
| `tag` | yes | Tag added to matching interfaces: 1 to 64 letters, digits, `.`, `_`, `:`, or `-`. |
| `interest` | no | `primary` or `secondary`. Leave it out to let annotations and rules decide. |
| `wan_circuit` | no | `true` marks matching interfaces as WAN circuits. |
| `sticky` | no | `true` pins matching interfaces to the top of the device page. |

To use **only** your own keywords, turn the packaged ones off:

```toml
[interface_classification]
use_default_keywords = false
```

A keyword that contains other characters, such as `WAN-ISP` or `UP*`, could never match a token, so validation rejects it. Use two keywords, or a `re:` keyword.

When several keywords match and disagree on interest, the **later keyword in the list wins**. Your keywords come after the packaged ones, so a `BACKUP` keyword with `interest = "secondary"` turns `WAN backup via LTE` into a secondary interface even though `WAN` also matched. `wan_circuit` and `sticky` are set when any matching keyword sets them.

### Interfaces without a keyword

Many interfaces have no keyword: user ports, SVIs, blank descriptions. Tags and annotations you add in vakr are stored separately from description tags, and a later description change never removes them ([META-013](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership)). An interface counts as **not yet classified** when it has no vakr annotation (no explicit interest, sticky, or WAN-circuit value and no vakr tag) and no keyword match. `vakr policy explain` reports it as `not yet classified: yes`. The device pages and interface list will offer the same filter, so you can work through the rest or fix the descriptions on the devices instead. Bulk tagging in the web UI, CSV/TOML import, and `vakr interface tag` arrive with the inventory milestones.

## Writing rules

Rules live in `/etc/vakr/policies.toml` as `[[interface_policy.rules]]` entries. The same table can appear in `config.toml` later; the format is identical.

```toml
[[interface_policy.rules]]
name = "access-user-ports"            # unique; shown in explain output
description = "User ports are not graphed"
groups = ["access-switches"]          # scope: device groups
priority = 0                          # optional, -1000..1000, higher wins in its tier
action = "exclude"                    # include | exclude
match.ifalias = ["exact:", "user port"]
```

Every rule needs a `name` and at least one of `action`, `settings`, or `annotations`. Unknown keys are errors, reported with their path, for example `interface_policy.rules[2].match.colour: unknown key`.

### Scope

The scope says what a rule applies to and sets its precedence tier. You can write `scope = "..."`, or leave it out and let the selector key imply it.

| Scope | Selector keys | Tier |
|---|---|---|
| `global` | none | global (lowest) |
| `group` | `groups = [...]` (device groups) | group/tag |
| `tag` | `tags = [...]` (device tags, vakr tags, and description tags) | group/tag |
| `site` | `sites = [...]` | group/tag |
| `device` | `devices = [...]` | device |
| `interface` | `devices = [...]` and `interfaces = [...]` (`ifName`) | interface override (highest) |

A rule has one scope. Put any further conditions under `match`.

### Match conditions

Every entry under `match` is a list of patterns. A condition matches when **any** pattern matches **any** value of the field (OR within the list). A rule matches when its scope and **every** condition match (AND across fields). A fact that is unknown matches nothing; vakr never guesses a value for missing data.

| Field | Matches | Case |
|---|---|---|
| `device` | Device inventory name | ignored |
| `device_address` | Poll address or DNS name | ignored |
| `site` | Device site | ignored |
| `device_tags`, `device_groups` | Device tags, device groups | ignored |
| `interface_tags` | Tags added in vakr, plus description tags | ignored |
| `tags` | Device tags plus `interface_tags` | ignored |
| `ifname`, `ifdescr`, `ifalias` | `ifName`, `ifDescr`, `ifAlias` | ignored |
| `iftype` | IANAifType number: `6` Ethernet, `24` loopback, `53` virtual/SVI, `131` tunnel, `161` port-channel | digits only |
| `ifindex` | Current `ifIndex`, as digits (a match field only; never identity) | digits only |
| `addresses` | Interface addresses as `address/prefix`, IPv6 in lowercase compressed form (`2001:db8::1/64`) | ignored |
| `provider`, `circuit_id` | Circuit metadata | ignored |
| `physical` | `true` or `false` (not a pattern list) | |
| `admin_status` | List of `up`, `down`, `testing` | |
| `oper_status` | List of `up`, `down`, `testing`, `unknown`, `dormant`, `not_present`, `lower_layer_down` | |

"Ignored" means globs and `re:` patterns ignore case. `exact:` patterns always compare case, on every field.

The form used in the blueprint's example, `exclude_ifname_patterns = ["Vlan*"]`, is shorthand for `action = "exclude"` plus `match.ifname = ["Vlan*"]`. It works for `include_` and `exclude_` with every field (`include_ifalias_patterns`, ...). Use one shorthand key per rule, without `action`.

### Actions

- `action = "include"` or `"exclude"` turns performance collection on or off. Interfaces are monitored by default.
- `annotations` sets `interest` (`"primary"` or `"secondary"`; `high_priority = true` means `primary`), `sticky`, and `wan_circuit`. Description keywords and your vakr annotations outrank these; see [Precedence](#precedence).
- `settings` changes collection settings, within the limits below.

### Settings and their limits

| Setting | Type | Allowed | Controls |
|---|---|---|---|
| `polling_interval` | duration | 30s to 1h | Interface poll interval |
| `discovery_interval` | duration | 30s to 30d | Rediscovery interval (default 24h, DISC-002) |
| `reachability_strategy` | `icmp_gated`, `snmp_fallback`, `icmp_only`, `snmp_only` | | How ICMP gates SNMP |
| `snmp_timeout` | duration | 1s to 30s | SNMP request timeout (default 2s) |
| `snmp_retries` | integer | 0 to 5 | SNMP retries (default 1) |
| `adaptive_polling` | `true`/`false` | | Adaptive interval changes |
| `stale_after` | duration | 1d to 365d | Time inactive before an interface is stale (default 10d) |

Durations are written `30s`, `5m`, `1h`, `1d`, or combined (`1h30m`). A value outside its range fails validation instead of overloading a device. Settings no rule changes keep the configured defaults. The 30-second minimum comes from the collection requirements; the other ranges are vakr's documented limits, proposed for Appendix E.

## Precedence

### Between rules

Rules are applied from the lowest precedence to the highest, and **a later matching rule overrides an earlier one** ([POLICY-002](../../tool-blueprint.md#84-collection-policy-requirements)):

1. **Tier:** global, then group/tag/site, then device, then interface override. A site is a set of devices, like a group, so site rules share the group/tag tier.
2. **Priority** within a tier: the higher number wins.
3. **File order** for equal tier and priority: the later rule wins. Write the general rule first and its exceptions after it, as in `.gitignore`.

Each value is resolved on its own. A tag rule can set `polling_interval` while a device rule sets `snmp_timeout`, and the interface gets both. A device rule that only sets `action` leaves the tag rule's interval alone.

### Interest, sticky, and WAN circuit

These three follow the classification order of [META-012](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership):

1. an explicit vakr annotation on the interface, then
2. a description keyword, then
3. the highest-precedence matching rule, then
4. the default: `secondary`, not sticky, not a WAN circuit.

So an interface described `ISP: ...` is primary even if a rule says `secondary`. To demote it, annotate it in vakr, or change the description.

## Pattern syntax and limits

| Syntax | Example | Meaning |
|---|---|---|
| glob (default) | `Gi*`, `Vlan1??`, `Port-channel[0-9]`, `Loopback[!0]` | `*` any text, `?` one character, `[...]` one of, `[!...]` none of. Matches the **whole** value. |
| `glob:` | `glob:re:x` | Explicit glob, for a value that starts with `re:` or `exact:`. |
| `exact:` | `exact:Vlan100`, `exact:` | Exactly this value, case included. `exact:` alone matches an empty value, such as a blank description. |
| `re:` | `re:(Gi\|Te)\d+/0/\d+` | Regular expression that must match the **whole** value, ignoring case. Write `.*WAN.*` to find `WAN` anywhere. |

Limits, checked when the file loads ([POLICY-005](../../tool-blueprint.md#84-collection-policy-requirements), CFG-005):

- A pattern is at most **256** characters, prefix included, and may not contain control characters.
- An unterminated `[` in a glob is an error. Shell tools would quietly treat it as a literal, which is almost always a typo.
- A list holds 1 to 256 patterns. A policy holds at most 2,000 rules, and the keyword list at most 256 keywords.
- Values longer than **512** characters never match a glob or `re:` pattern, and descriptions longer than that are not classified. SNMP values are far shorter: `ifAlias` is at most 64 characters (RFC 2863) and `ifName`/`ifDescr` at most 255. Explain output says so when this limit applies.
- `re:` patterns must compile, and must not be able to backtrack catastrophically. vakr refuses:

| Refused | Example | Why | Write instead |
|---|---|---|---|
| Nested quantifier | `(a+)+`, `(\w+\s?)*` | Exponential time on a near-miss | `a+`, or make the inner repeat possessive: `(\w++\s?)*` |
| Overlapping alternatives under a repeat | `(ab\|\wc)+` | Exponential | Make the alternatives start differently, or use `(?>...)` |
| A repeat of something that can be empty | `(a*)*` | Endless empty matches | `a*` |
| More than 2 open-ended repeats that overlap | `.*a.*b.*c` | Cubic time and worse | `.*a.*b`, or several rules |
| More than 1,024 combinations of overlapping optional parts | `a?a?a?…` | Exponential in the pattern length | Bounded repeats such as `a{0,12}` |
| Overlapping optional parts around open-ended repeats | `a?a?a?a?.*a.*c` | Each choice multiplies the repeats' cost | Drop the optional parts or one repeat |
| Backreferences and conditionals | `(a)\1`, `(?(1)a\|b)` | Not needed for names; NP-hard in general | |

"Overlap" means the characters that can start one part can also start what follows it, so the regex engine has a real choice to backtrack over. `\d+/` has none, because a digit is never `/`. `.*a` does. Letters, digits, and spaces never overlap one another, so `(\w+\s)+` and `(\D+\d)+` are fine. Patterns such as `(Gi|Te)\d+/\d+/\d+`, `(\d{1,3}\.){3}\d{1,3}`, `.*ISP.*WAN.*`, and `[^/]+/[^/]+` are accepted and finish in microseconds even on worst-case 512-character input.

### Why patterns are checked, not timed

Python's `re` module has no match timeout and holds the interpreter lock while it runs, so one bad pattern could freeze the whole poller. Two remedies were considered:

- **The third-party `regex` module and its `timeout=`** bounds each match in wall time. But it adds a compiled dependency to a self-contained package, and a timeout limits each call rather than the total: a 100 ms timeout across 1,600 interfaces and several rules could still cost minutes per cycle. A pattern that times out also fails while the poller runs, not when you save the file.
- **Static rejection plus an input cap**, using only the standard library. vakr analyses the parsed pattern for the shapes above and caps the input at 512 characters. Every accepted pattern then has a known worst case: about two million backtracking steps on a 512-character value, which measured under 10 ms on the slowest pattern the limits allow. A bad pattern fails `vakr policy validate` with a reason before anything runs.

vakr uses the second. The analysis can refuse an unusual pattern that would have been safe; rewrite it as the table suggests.

## Checking and explaining

### `vakr policy validate`

```sh
vakr policy validate /etc/vakr/policies.toml
```

This checks the policies file and the description keywords in the effective configuration (`--config-dir`, default `/etc/vakr`). It prints `policies valid: 4 rule(s) in /etc/vakr/policies.toml; 3 description keyword(s)` and exits 0, or lists every problem with its file and path and exits 1:

```text
vakr policy: bad.toml: interface_policy.rules[0].match.ifalias[0]: nested quantifier: a repeated part that can overlap what follows it sits inside another repeat, which can take exponential time; simplify it or make the inner repeat possessive (*+, ++)
vakr policy: bad.toml: interface_policy.rules[0].action: expected one of: include, exclude
vakr policy: bad.toml: interface_policy.rules[0].settings.polling_interval: must be between 30s and 1h
vakr policy: 3 problem(s) found
```

### `vakr policy explain`

```sh
vakr policy explain --facts edge-facts.toml [--policies FILE] [--json]
```

Until vakr has its own inventory (milestones M3/M4), explain reads the interfaces from a facts file in TOML, or in JSON when the name ends in `.json`. Without `--policies` it uses `policies.toml` in the configuration directory, if there is one. Exit status is 0 on success, 1 on validation errors, and 2 on usage errors. The same input always prints the same output ([POLICY-006](../../tool-blueprint.md#84-collection-policy-requirements)).

A facts file lists interfaces; the keys match the rule match fields ([full example](../../tests/unit/policy/data/edge-facts.toml)):

```toml
[[interfaces]]
device = { name = "edge-rtr1", address = "192.0.2.1", site = "nyc01", groups = ["wan-routers"] }
interface = { ifindex = 20, ifname = "Vlan100", ifalias = "", iftype = 53, physical = false, admin_status = "up", oper_status = "up" }
annotations = { tags = ["mgmt"] }          # optional: interest, high_priority, sticky, wan_circuit, display_rank, tags
circuit = { provider = "Carrier-X" }       # optional: provider, circuit_id, contracted_bps
```

Status values may be names (`lower_layer_down` or `lowerLayerDown`) or the numbers the agent sends (`7`).

The output for `Vlan100`, with the [example policies](../../tests/unit/policy/data/policies.toml):

```text
interface edge-rtr1 Vlan100 (ifIndex 20)
  decision: monitored [rule edge-mgmt-svi]
  description: ""
    tokens: (none)
    keywords: (none)
  description tags: (none)
  operator tags: (none)
  not yet classified: yes
rules from /etc/vakr/policies.toml, lowest precedence first (a later match overrides):
  #1 no-logical-noise [global tier, priority 0]: matched
      match ifname: "Vlan100" matches Vlan*
      overridden: monitor
  #2 access-user-ports [group tier, priority 0]: no match
      decided by scope groups: "wan-routers" matches none of access-switches
  #3 wan-fast-poll [tag scope, group tier, priority 0]: no match
      decided by scope tags: (none) matches none of wan, isp
  #4 edge-mgmt-svi [interface tier, priority 0]: matched
      scope devices: "edge-rtr1" matches edge-rtr1
      scope interfaces: "Vlan100" matches exact:Vlan100
      sets: monitor
values:
  monitor = include from rule edge-mgmt-svi
      why: highest-precedence matching rule (interface tier, priority 0, #4)
      overrides: exclude from rule no-logical-noise
      overrides: include (default)
  interest = secondary (default)
      why: no annotation, keyword, or rule sets it; interest defaults to secondary (META-011)
  sticky = false (default)
      why: no annotation, keyword, or rule sets it
  wan_circuit = false (default)
      why: no annotation, keyword, or rule sets it
```

How to read it:

- **decision** says whether the interface is graphed and which rule decided that, or `[default]`. A suppressed interface reads `suppressed (not monitored; still inventoried)`.
- **rules** lists every rule, in the order applied. A rule that did not match shows the first condition that failed (`decided by ...`) with the values it tested. A rule that matched shows each condition, the values it set (`sets`), and the values a later rule or a higher source replaced (`overridden`).
- **values** gives each effective value, where it came from (`rule`, `keyword`, `annotation`, or `default`), why that source won, and the offers it beat, highest first.

`--json` prints the same information as `{"explanations": [...]}`: `interface`, `decision`, `classification`, `rules` (with `decided_by` and every condition), and `values`. Durations are given in seconds.

## Worked examples

These follow the NetOps convention of labelling interfaces of interest `ISP`, `WAN`, or `UPLINK` in their descriptions.

### Uplinks and circuits: nothing to write

With the packaged keywords, `ISP: Carrier-X 1G ckt ABC12345` and `[WAN] MPLS primary` are primary WAN circuits, and `UPLINK to core-sw1 Te1/0/1` is a primary uplink. They are monitored, listed first on the device page, and tagged `isp`, `wan`, or `uplink`, with no rule at all.

### Poll WAN circuits more closely and pin them

Description tags can be used as scopes, so the keyword convention also drives settings:

```toml
[[interface_policy.rules]]
name = "wan-fast-poll"
tags = ["wan", "isp"]
settings.polling_interval = "30s"
annotations.sticky = true
```

### Suppress logical interfaces, with one exception

```toml
[[interface_policy.rules]]
name = "no-logical-noise"
exclude_ifname_patterns = ["Loopback*", "Vlan*", "Tunnel*", "Null*"]

[[interface_policy.rules]]
name = "edge-mgmt-svi"
devices = ["edge-rtr1"]
interfaces = ["exact:Vlan100"]
action = "include"
```

The override wins because the interface tier outranks the global tier. It would also win from a later position in the same tier.

### Quiet access switches

```toml
[[interface_policy.rules]]
name = "access-user-ports"
groups = ["access-switches"]
action = "exclude"
match.ifalias = ["exact:", "user port"]
```

User ports and ports with blank descriptions on access switches are not graphed. Their `UPLINK` ports still are, and they stay in the inventory, so `vakr policy explain` and the "not yet classified" filter still show them.

### A slow satellite site

```toml
[[interface_policy.rules]]
name = "satellite-site"
sites = ["remote-sat1"]
settings.snmp_timeout = "10s"
settings.snmp_retries = 2
settings.polling_interval = "5m"
```

### A description that doesn't follow the convention

`SWANSEA office link` does not match `WAN`, because keywords match whole tokens. Either fix the description on the device (`WAN SWANSEA office link`), add a keyword (`keyword = "SWANSEA"`, `tag = "wan"`, `interest = "primary"`), or annotate the interface in vakr. The annotation outranks everything else.
