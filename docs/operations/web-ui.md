# The web UI

vakr's web UI shows devices, interfaces, graphs, rankings, and vakr's own health in a browser. `vakr-api` serves it at the same address as the API, so once [web access](web-access.md) is set up, open `https://vakr.example.net/` and sign in. There is nothing else to install or start: the package ships the built pages in `/opt/vakr/share/web/`, and the browser needs no plug-in and loads nothing from the internet.

- [Signing in and out](#signing-in-and-out)
- [Pages](#pages)
- [What the page states mean](#what-the-page-states-mean)
- [Graphs](#graphs)
- [Keyboard and screen readers](#keyboard-and-screen-readers)
- [Refresh](#refresh)
- [Who sees what](#who-sees-what)
- [Not in this release](#not-in-this-release)
- [Troubleshooting](#troubleshooting)

## Signing in and out

Sign in with a local account: `sudo vakr setup` creates the first admin, and `sudo vakr user add` creates others ([users](users.md)). The browser keeps a session cookie that scripts cannot read; it ends after 30 minutes without use or 12 hours at most (Appendix E.12), or when you choose **Sign out**.

| You see | Why | What to do |
|---|---|---|
| "Sign-in failed: check the username and password." | The account or password is wrong, or the account is disabled. vakr does not say which. | Try again, or ask an admin to check `vakr user list`. |
| "Too many failed sign-ins: wait 30 s and try again." | Repeated failures for this account from this address are slowed down (AUTH-004). | Wait the time shown. |
| "Sign-in needs HTTPS" | You reached vakr over plain HTTP from another machine. | Use the HTTPS address of the proxy. |
| "Your session ended" on the sign-in page | The session expired, was idle too long, or was signed out elsewhere while a page was open. | Sign in; you return to the page you were on. |

## Pages

The bar at the top has every section, a search box, your name and role, the release, and **Sign out**. The current section is underlined and announced as the current page.

| Page | Address | What it shows |
|---|---|---|
| Overview | `/` | Device counts by state and evidence freshness, collector and database health, high-priority interfaces, WAN circuits, top utilization and top errors for 15 minutes to 24 hours, recent events, and quick filters by provider, site, tag, and group. |
| Devices | `/devices` | Every device in Attention, Active, Offline/Retired order (blueprint 7.7), with state, address, site, ICMP and loss, SNMP, agent uptime, interfaces, last poll, evidence age, collector, tags, and interval. Filter by status, freshness, site, tag, group, collector, provider, and admin state; sort by state, name, site, or age; choose the page size and compact rows. The filters stay in the address, so you can bookmark or share a view. |
| A device | `/devices/<uuid>` | The header (state, address, site, tags, admin state, collector and last poll, each position's own verdict), every fact vakr has (ICMP, latency, jitter, SNMP, `sysUpTime` with its disclaimer, intervals, comment), the reachability graph, the interfaces, and open identity reviews. **Table** lists the high-priority interfaces in full and the others on request (searchable, 25 per page, sticky and WAN circuits first); **Graphs** shows a small traffic graph per interface, 12 per page. |
| Interfaces | `/interfaces` | Every interface, sticky, WAN, and high-priority first, with state, lifecycle, rates, utilization, packets, errors, capacity, addresses, and age. Filter by site, tag, group, provider, WAN, interest, lifecycle, and the collector whose values are shown. |
| An interface | `/interfaces/<uuid>` | Identity and circuit metadata, current values, traffic, utilization, packets, and errors over a chosen range, the admin/oper state timeline, and the identity history with earlier `ifIndex` values. |
| Sites | `/sites` | Sites, tags, groups, providers, and admin states, each leading to the matching devices or interfaces. |
| Dashboards | `/dashboards` | The built-in dashboards: overview, rankings, high priority, WAN circuits. |
| Rankings | `/rankings` | Top utilization (p95 of the window against capacity) and top errors, 10 to 100 rows, with coverage per row, interfaces left out and why, and CSV/JSON downloads. |
| High priority | `/high-priority` | Every high-priority (primary-interest) interface. |
| WAN circuits | `/wan` | Circuits with provider, circuit ID, contracted rate, and current use. |
| Collectors/Health | `/collectors` | Each collector with its state, report age, and spool use, and the API's own components. |
| Identity reviews | `/identity-reviews` | Interfaces that discovery could not match with certainty. Admins decide them here. |
| Search | `/search?q=` | Devices, interfaces, sites, providers, circuits, tags, and groups whose names contain the text. Every hit opens its page, so any interface's history is two steps from the search box. |
| Admin | `/admin` | Admin tasks (admins only). |

## What the page states mean

Every page says what it knows and what it does not. Colour always comes with an icon and words.

| State | You see | Means |
|---|---|---|
| Loading | "Loading …" | The first answer has not arrived. |
| Empty | A gray note, such as "No device matches these filters." | The answer is complete and has nothing in it. |
| Stale | An amber **Stale** note | The last refresh failed and the page still shows the previous answer, with its time; or the newest evidence for a device is older than the freshness threshold. The values are not current. |
| Partial | An amber **Partial** note listing what is missing | Telemetry cannot be read, a collector is unhealthy or silent, or an answer reached its size bound. Devices without fresh evidence show **Unknown**, never **Down**. |
| Permission denied | "Permission denied" | Your role cannot see or do this. |
| Backend unavailable | A red "Backend unavailable" note | `vakr-api` or its database did not answer. This is about vakr, not the network; the page retries on its own. |

Device states use the colours of blueprint 12.8: red for confirmed trouble (✖ Down), amber for degraded or unknown (▲ Packet loss, ? Unknown), green for healthy and fresh (✔ OK), and gray for administratively offline (○ Disabled). No icon is used for two colours, so the states read in grayscale.

## Graphs

Graphs follow blueprint 12.6:

- **Range.** Choose 1h, 6h, 24h, 7d, 30d, 90d, or 400d, or **Custom…** for a range of 1 minute to 400 days, typed in the display time zone. The range stays in the address.
- **Time zone.** Times are in vakr's display zone (`time.timezone`), named under each graph with the site's zone when it differs.
- **Resolution.** Under each graph: native samples, native samples in buckets, or 15-minute rollups (used beyond 30 days). Points are capped at 1,200 per series; when the cap applies, the page says so.
- **Missing data.** A missing or invalid value is a break in the line, never a drop to zero. Shaded stretches mark quality problems, each with a letter: G gap, B invalid counter baseline, R agent restart, D counter discontinuity, P partial collection, C collector trouble, O above capacity. **Quality markers** lists them with the reason.
- **Inbound and outbound.** Inbound is a solid line over a light fill; outbound is a dashed line. Two collector positions differ by dash pattern as well. Each position is its own series; vakr never merges them.
- **Latency.** One graph per collector position: every probe's round-trip time as a dot (the "smoke"), the median as a line that breaks where all probes were lost, and loss as bars against the right-hand axis. Beyond 1,200 polls, a band spans the lowest to highest median instead of the dots.
- **Capacity.** On traffic graphs, **Show capacity** draws the capacity and contracted-rate lines.
- **Exact values.** Under each graph a table gives the last, minimum, mean, and maximum of each series, and how many points were missing. The **Read values point by point** slider moves the cursor along the graph with the keyboard and reads out the time and every value.
- **Downloads.** **CSV** and **JSON** download the same window from the API (at most 20,000 rows and 4 MiB).
- **State timeline.** The interface page shows admin and oper state over the last 7 days of the range, with a table of every segment.

## Keyboard and screen readers

- **Tab** reaches every control, and the focused one is outlined. The first **Tab** reaches **Skip to content**.
- Table headers that sort are buttons: **Enter** sorts, the header says its order to screen readers, and the change is announced ("Sorted by Name, A to Z").
- Graph ranges are a group of buttons; the chosen one is marked pressed.
- Each graph has a text description, a table of exact values, and the point-by-point slider.
- Statuses are read as words ("Down", "Unknown — collector unavailable"); the icons are decorative.
- With "reduce motion" set in the operating system, nothing animates.

## Refresh

Pages refresh themselves while the browser tab is visible, and stop while it is hidden (Appendix E.31):

| Page | Every |
|---|---|
| Overview, device and interface lists, high priority, WAN | 30 s |
| Device and interface pages (current values), collectors | 30 s |
| Graphs | 60 s |
| Rankings, identity reviews | 60 s |

A refresh replaces values in place: the page does not flash, the keyboard focus stays where it was, and unchanged content is not announced again. A failed refresh is retried twice (after 1 s and 2 s) when vakr-api did not answer, and the page then shows the stale note until a refresh succeeds.

## Who sees what

Operators see every page. Admins also see **Admin** and the decision buttons on identity reviews. vakr-api checks every request itself; hiding a button is not the control ([API: who may do what](api.md#who-may-do-what)).

To answer an identity review, an admin chooses **It is \<interface\>** (the row is that interface, and its history continues) or **It is a new interface**, then confirms. The decision is recorded with the admin's name in the identity history and the audit log.

## Not in this release

Building and pinning your own dashboards, public dashboards, kiosk tokens, editing devices and interfaces, rediscovery requests, policy explanations, and live updates (server-sent events) come later. Inventory changes are made in `/etc/vakr/inventory.toml` ([inventory](inventory.md)).

## Troubleshooting

| Symptom | Check |
|---|---|
| `https://vakr.example.net/` answers `{"code":"not_found",...}` | The web files are missing. `journalctl -u vakr-api` names the directory in an `api.web_unavailable` line; reinstall the package. |
| The page is blank | Use a current browser with JavaScript on. The page loads only files from vakr's own address; a proxy that rewrites pages or adds scripts will be blocked by the page's Content-Security-Policy. |
| Everything shows Unknown | Read the **Partial** note: telemetry unavailable or a silent collector. `vakr check health` and the Collectors/Health page say which. |
| Times look an hour off | Times are in vakr's display zone, named under each graph. Set `time.timezone` in `/etc/vakr/config.toml` ([configuration](configuration.md)). |
