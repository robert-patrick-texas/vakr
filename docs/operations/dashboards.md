# Custom dashboards

Besides the built-in dashboards (overview, rankings, high-priority interfaces, WAN circuits), admins can build their own: the uplinks of every core switch on one page, the WAN circuits of one provider, the devices of one site. Everyone signed in can view them, and kiosk displays can show them, alone or in a rotation.

- [Viewing a dashboard](#viewing-a-dashboard)
- [Building one](#building-one)
- [Pinning an interface from its page](#pinning-an-interface-from-its-page)
- [Widgets](#widgets)
- [When someone else changed it](#when-someone-else-changed-it)
- [On a kiosk display](#on-a-kiosk-display)
- [Retired and missing interfaces](#retired-and-missing-interfaces)
- [The widget format](#the-widget-format)
- [Limits](#limits)
- [Not in this release](#not-in-this-release)

## Viewing a dashboard

**Dashboards** in the navigation lists the built-in dashboards and then the custom ones. A custom dashboard shows its widgets in the order the admin chose. The line under the title says when the data was read, which position (collector) it comes from, how often it refreshes, and the dashboard's kiosk address.

A refresh is one request to vakr-api, whatever the dashboard holds; graph cards load as they scroll into view. When a refresh fails, the page keeps what it had and says it is stale, as every page does ([web UI](web-ui.md#what-the-page-states-mean)).

## Building one

Only admins create, change, and delete dashboards. Operators see the same pages without the controls; the API refuses them anyway.

1. **Dashboards** → **New dashboard**.
2. Give it a name (unique, up to 80 characters), an optional description, and a refresh interval (15 s to 1 h; 60 s by default).
3. Add widgets: choose the kind under **Add a widget** and select **Add**. Give each a title; move widgets up and down, or remove them.
4. **Save**. The editor lists what is missing before it lets you save, for example a pinned widget without interfaces or a selection without a filter.

To change a dashboard, open it and select **Edit**. **Delete dashboard** in the editor removes it after a confirmation; kiosk displays showing it say so at their next refresh.

Every create, change, and delete is recorded in the audit log with the admin's name (SEC-018).

## Pinning an interface from its page

An interface's page has **Pin to dashboard** for admins. Choose a dashboard and select **Pin**: the interface joins the first pinned-interfaces widget with room, or a new one at the end. If someone saved that dashboard at the same moment, the page says so; pin it again.

## Widgets

| Widget | Shows | Settings |
|---|---|---|
| Pinned interfaces | Interfaces you choose, across devices, in your order: state, rates, utilization, errors, age, and optionally a 24-hour graph card for each | Find interfaces with the search box (name, alias, description, device) and **Pin** them; order them with the arrows; **Show a graph card per interface** |
| Interfaces | Every interface matching filters, high-priority, WAN, and sticky first | Site, tag, group, provider, interest (high or normal priority), WAN circuits only; at least one; rows shown (1 to 100, 25 by default) |
| Devices | Every device matching filters, Attention first, with Attention/Active/Offline counts | Site, tag, group; at least one; rows shown |
| Ranking | Top utilization (p95) or top errors among the interfaces matching filters, exactly as the rankings page computes it | Ranking, window (15m, 1h, 6h, 24h), rows (1 to 25), filters (none means every interface) |

A selection that matches more than it shows says "Showing 25 of 140; 115 more not shown". A tag matches an interface tagged with it or on a device tagged with it, as on the interfaces page.

## When someone else changed it

The editor saves against the version it opened. If another admin saved the dashboard in between, saving is refused with "Someone changed this dashboard since you opened it" and **Reload the dashboard** opens their version; apply your change again. Nothing is overwritten silently.

## On a kiosk display

A custom dashboard is at `/kiosk/dashboard/<uuid>`; its page shows the address. On a display it looks like the built-in kiosk dashboards: no navigation, names as plain text, the freshness line, and full-screen messages when the token expires or vakr-api stops answering ([kiosk displays](kiosk.md)). Without `?refresh=` it refreshes at the dashboard's own interval.

In a rotation, a custom dashboard is `dashboard:<uuid>`:

```text
/kiosk/rotate?pages=overview,dashboard:0190a3c4-0000-7000-8000-000000000001,wan&every=60
```

The rotation names it by its dashboard name ("next: Core uplinks in 40 s") and reads it a few seconds before its turn, like the built-in ones. If the dashboard is deleted, the display says so in full instead of skipping it.

## Retired and missing interfaces

A pinned interface that is retired keeps its row with its names and "Retired: no current values". One that no longer exists (after restoring an older backup, for example) keeps its row as "No longer exists". Neither disappears silently or breaks the dashboard; an admin removes them in the editor.

## The widget format

The API takes and returns widgets as JSON (widget schema 1); unknown fields are refused, so a typo never drops a filter silently:

```json
[
  {"kind": "pinned_interfaces", "title": "Uplinks",
   "interfaces": ["<interface uuid>", "<interface uuid>"], "graphs": true},
  {"kind": "interface_selection", "title": "Lumen at Dallas",
   "criteria": {"site_id": "<site uuid>", "provider": "Lumen", "wan_only": true}, "limit": 25},
  {"kind": "device_selection", "title": "Backbone", "criteria": {"group_id": "<group uuid>"}},
  {"kind": "ranking", "title": "Busiest WAN", "ranking": "utilization",
   "criteria": {"wan_only": true}, "window": "1h", "limit": 10}
]
```

Interface criteria are `site_id`, `tag_id`, `group_id`, `provider`, `wan_only`, and `interest` (`primary` or `secondary`); device criteria the first three. UUIDs come from `GET /api/v1/sites`, `/tags`, `/groups`, and the interface endpoints ([API](api.md#endpoints)). A UUID that does not exist is refused with the widget's field path, such as `widgets[1].criteria.site_id`.

Stored dashboards keep the schema version that wrote them. A later release that changes the format upgrades older dashboards when it reads them; a release older than the dashboard refuses to read it rather than guess, so going back to an older release can make newer dashboards unreadable until you update again.

## Limits

| Limit | Value |
|---|---|
| Dashboards | 200 |
| Name, description, widget title | 80, 500, 80 characters |
| Widgets per dashboard | 12, of which at most 2 rankings |
| Pinned interfaces per widget | 50 |
| Rows of a selection | 1 to 100 (25 by default) |
| Rows of a ranking | 1 to 25 (10 by default) |
| Refresh | 15 s to 1 h |
| Graph cards per page | 12, for the first live pinned interfaces |

These are the bounds of Appendix E.31.

## Not in this release

- **Dashboards from a file.** Every dashboard is managed through the web UI or the API. `/etc/vakr/dashboards.toml` comes later; each dashboard already records who manages it, so file-managed ones will not need a migration.
- **Public dashboards.** Publishing a dashboard for anonymous viewers is deferred with public dashboards.
- **A dashboard command.** There is no `vakr dashboard` command; use the web UI, or the API over the local socket as root or a member of `vakr-admin`.
