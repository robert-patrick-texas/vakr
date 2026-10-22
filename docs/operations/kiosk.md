# Kiosk displays

A kiosk display is a browser on a wall screen, such as a NOC display, that shows one of vakr's dashboards, or several in turn, without anyone signing in. An admin creates a **kiosk token** for it once. The display pastes the token once and stays signed in, read-only, until the token expires or is revoked.

- [What a display shows](#what-a-display-shows)
- [Creating a token](#creating-a-token)
- [Setting a display up](#setting-a-display-up)
- [Choosing the dashboard](#choosing-the-dashboard)
- [Rotating through dashboards](#rotating-through-dashboards)
- [What the screen says](#what-the-screen-says)
- [Replacing a token before it expires](#replacing-a-token-before-it-expires)
- [Revoking a token](#revoking-a-token)
- [Security](#security)
- [Limits](#limits)

## What a display shows

The four built-in dashboards of the [web UI](web-ui.md), without the navigation bar, with larger type, and with device and interface names as plain text, so a stray tap never navigates the display away:

| Dashboard | Address | Reads per refresh |
|---|---|---|
| Overview: device state, events, vakr's health, high-priority interfaces, WAN circuits, top utilization and errors | `/kiosk/overview` | 1 |
| High-priority interfaces (the first 100) | `/kiosk/high-priority` | 1 |
| WAN circuits | `/kiosk/wan` | 1 |
| Top utilization and top errors, 10 rows each | `/kiosk/rankings` | 2 |

Each dashboard says when its data arrived and how often it refreshes: "Data as of 14:05:09 · refreshes every 30 s". A refresh updates the rows in place; nothing flashes or reloads.

## Creating a token

On the monitoring node:

```console
$ sudo vakr kiosk create noc-wall
created kiosk token noc-wall, expires 2026-11-20 14:03 UTC (id 0190a3c4-...)

This is the only time the token is shown; vakr keeps only its digest:

    vkk_...

On the display, open <your vakr address>/kiosk/setup and paste the token.
Revoke it with: sudo vakr kiosk revoke noc-wall
```

- The label names the display: 1 to 64 letters, digits, spaces, `.`, `_`, or `-`, starting with a letter or digit. Each label is used once.
- The token lives `auth.kiosk_token_lifetime` (30 days by default). `--lifetime 90d` sets another lifetime for this token, from `1d` to `365d`.
- The token is printed once and never again: `vakr kiosk list` shows labels, expiry, and last use, never tokens. If it is lost, revoke it and create another.
- An admin can do the same in the web UI: **Admin** → **Kiosk tokens** shows the new token once, with a copy button.

At most 100 kiosk tokens can exist. Every create, revoke, and display setup is in the audit log ([users](users.md#audit-records)).

## Setting a display up

1. On the display's browser, open `https://vakr.example.net/kiosk/setup` (the [reverse proxy's](web-access.md) HTTPS address).
2. Paste the token into **Kiosk token** and choose **Set up**. The field hides what you paste.
3. The display goes to the kiosk chooser. Pick a dashboard or a rotation, and make that address the browser's start page.

The browser keeps an `HttpOnly` cookie, so the display survives reboots and power cuts without being set up again. Setup works only over HTTPS, or over plain HTTP on the monitoring node itself; the token is never part of an address, so it does not end up in history or bookmarks.

For a dedicated display, start Chromium in kiosk mode with a profile of its own, so the cookie persists:

```console
$ chromium --kiosk --user-data-dir=/var/lib/noc-display/chromium \
    --noerrdialogs --disable-session-crashed-bubble \
    'https://vakr.example.net/kiosk/rotate?pages=overview,wan,rankings&every=60'
```

Set the display up once with that profile (for example by opening `/kiosk/setup` first, with a keyboard attached), then use the command above in the display's autostart.

## Choosing the dashboard

`/kiosk` lists the dashboards. Two options go on any kiosk address:

| Option | Meaning | Allowed |
|---|---|---|
| `refresh=SECONDS` | How often the data is read. Without it, each dashboard keeps its own interval: 30 s, rankings 60 s. | 15 to 3600 |
| `window=15m` | The ranking window of the overview and the rankings. | `15m`, `1h`, `6h`, `24h` |

For example, `/kiosk/rankings?window=1h&refresh=120`. A value outside these bounds, or an unknown dashboard name, fills the screen with what is allowed instead of showing something else.

A custom dashboard ([dashboards](dashboards.md)) is at `/kiosk/dashboard/<uuid>`; its page in vakr shows that address. Without `refresh` it keeps the interval the admin set on it.

## Rotating through dashboards

```text
/kiosk/rotate?pages=overview,wan,rankings&every=60
```

| Option | Meaning | Allowed |
|---|---|---|
| `pages` | The dashboards, in order, separated by commas. A dashboard may appear more than once, to show it more often: `overview,wan,overview,rankings`. | 2 to 8 names of `overview`, `high-priority`, `wan`, `rankings`, or `dashboard:<uuid>` for a custom dashboard |
| `every` | Seconds each dashboard is shown. | 15 to 3600; 60 by default |
| `refresh`, `window` | As above, for every dashboard of the rotation. | as above |

- The header says where the rotation is: "2 of 3 · next: WAN circuits in 40 s".
- Only the dashboard on screen reads data, at its own interval, so a rotation costs no more than the dashboard being shown. Five seconds before a switch, the next dashboard reads its data, so it appears complete instead of loading.
- Switching does not slide or fade; one dashboard replaces the other.
- The rotation pauses while the browser tab is hidden, and **Pause rotation** stops it until **Resume rotation**.

## What the screen says

| The display shows | Why | What to do |
|---|---|---|
| "Stale: The last refresh failed …; showing the answer from 14:05:09." | vakr-api did not answer the last refresh, so the rows are older than they look. | Check `systemctl status vakr-api` and the proxy. The display keeps trying. |
| "vakr-api is not answering" (whole screen) | The display could not reach vakr-api when it started. | As above; the display asks again every 15 s. |
| "This display is not signed in" (whole screen) | The token was never set up on this browser, has expired, or was revoked. | Create a new token and paste it at `/kiosk/setup`. |
| "This display's kiosk token expires on …" | Fewer than 7 days are left. | [Replace the token](#replacing-a-token-before-it-expires). |
| "This kiosk address has a mistake" (whole screen) | An option is out of bounds or a dashboard name is unknown. | Correct the address; the message lists what is allowed. |
| "Partial: Telemetry unavailable …" | vakr cannot read its telemetry database. Device states are Unknown, never Down. | See [database](database.md). |

## Replacing a token before it expires

Kiosk tokens expire after 30 days by default, so a display needs a new one regularly:

1. `sudo vakr kiosk create noc-wall-2` (labels are unique, so use a new one).
2. On the display, open `/kiosk/setup` and paste the new token. The new cookie replaces the old one.
3. `sudo vakr kiosk revoke noc-wall` to end the old token.

The display warns for its last 7 days. A longer `--lifetime` (up to `365d`) means fewer replacements, at the cost of a token that stays valid longer if it is ever copied.

## Revoking a token

```console
$ sudo vakr kiosk revoke noc-wall
revoked kiosk token noc-wall; displays holding it are signed out
```

`revoke` takes the label or the ID from `vakr kiosk list`. Every display holding the token is signed out at its next request and shows "This display is not signed in". **Forget this display** on `/kiosk` removes the cookie from one browser only; the token stays valid on any other display until it is revoked.

## Security

- A kiosk token is the **operator** role: it reads everything an operator can, and changes nothing. Someone at the display's keyboard can reach the normal pages; give a display only to a screen in a place you would trust with read access.
- The browser keeps the token in an `HttpOnly`, `SameSite=Strict` cookie that page script cannot read. vakr keeps only its SHA-256 digest. The token is shown once, on creation, and never appears in logs, audit records, or field bundles.
- If a person signs in on the same browser, their session wins over the display's cookie until they sign out.
- Programs that are not browsers can send the token as `Authorization: Bearer vkk_...` ([API](api.md#who-may-do-what)).

## Limits

| Limit | Value |
|---|---|
| Kiosk tokens | 100 |
| Lifetime | 1 to 365 days; `auth.kiosk_token_lifetime` (30 days) by default |
| Refresh | 15 s to 1 h |
| Rotation | 2 to 8 dashboards, 15 s to 1 h each |
| Setups being checked at once | 4, with 16 waiting; a refused token is answered after 1 s |

These are the bounds of Appendix E.12, E.30, and E.31.
