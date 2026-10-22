# The API service

`vakr-api.service` serves vakr's JSON API, version 1. The [web UI](web-ui.md), `vakr tui`, the CLI, and your own integrations all read monitoring state through it. The same service serves the web UI's pages at every path outside `/api/`. It reads telemetry, which only the [collector](collector.md) writes, and it writes metadata: sign-in sessions, audit records, and administrator decisions.

It listens in two places:

| Listener | Default | Who uses it | How callers are identified |
|---|---|---|---|
| TCP, plain HTTP | `127.0.0.1:8080` | A reverse proxy on the same host, which terminates TLS ([web access](web-access.md)) | Session cookie from a sign-in, or a kiosk token |
| Unix socket | `/run/vakr/api.sock` | `vakr status`, `vakr tui`, `vakr check health`, and scripts on the node | The connecting account (`SO_PEERCRED`) |

- [Starting and stopping](#starting-and-stopping)
- [Startup checks](#startup-checks)
- [Who may do what](#who-may-do-what)
- [The local socket](#the-local-socket)
- [Signing in over HTTP](#signing-in-over-http)
- [Conventions every endpoint follows](#conventions-every-endpoint-follows)
- [Endpoints](#endpoints)
- [Not in this release](#not-in-this-release)
- [OpenAPI document](#openapi-document)
- [Health and monitoring the API](#health-and-monitoring-the-api)
- [Limits](#limits)
- [Examples](#examples)

## Starting and stopping

The package installs the unit but does not enable it (PKG-003). To start the API now and at every boot:

```console
$ sudo systemctl enable --now vakr-api.service vakr.target
```

| Task | Command | What happens |
|---|---|---|
| Start | `sudo systemctl start vakr-api` | The startup checks run, both listeners open, and only then does the unit count as started. |
| Stop | `sudo systemctl stop vakr-api` | New connections are refused, requests in progress get 10 seconds to finish, the databases are closed, and the socket file is removed. |
| Restart | `sudo systemctl restart vakr-api` | Every `[api]` and `[auth]` option takes effect on restart. There is no reload: `SIGHUP` is logged and ignored. |
| Status | `systemctl status vakr-api` | Running or not, and the last status line. |
| Log | `journalctl -u vakr-api` | One line per event, with a stable event ID such as `[api.ready]`. |

The API runs as the `vakr` user with no capabilities. The file system is read-only to it except `/var/lib/vakr` (the metadata database and the installation key) and `/run/vakr` (the socket). It can run while the collector runs: both open the SQLite databases in WAL mode, and the API only reads telemetry. Its view of the telemetry database refuses every write in code, too.

Log lines worth knowing:

| Event ID | Meaning |
|---|---|
| `api.ready` | Both listeners are open. |
| `api.preflight_failed` | A startup check failed; the line names it and the fix. |
| `api.request` | One request: method, path, status, duration, client, request ID. Failures and changes are logged at info level; successful reads only with `--debug`. Query strings, headers, cookies, and bodies are never logged. |
| `api.request_failed` | A request failed inside vakr; the line names the request ID the client saw, and the exception type. |
| `api.untrusted_forwarding` | A client sent `X-Forwarded-*` headers but is not in `api.trusted_proxies`; the headers were ignored. Logged once. |
| `api.purge_failed` | Expired sessions could not be deleted this time; the next pass retries. |
| `api.stopped` | A clean stop. |

## Startup checks

If a check fails, the API logs one sentence naming the check and the fix, exits with status 78, and systemd does not restart it.

| Check | Fails when | Fix |
|---|---|---|
| `config` | A configuration file is invalid, or `[api]` holds a bad address, a relative socket path, or a malformed proxy network | `vakr config validate` |
| `state_dir` | `/var/lib/vakr` is missing or not writable by `vakr` | Reinstall, or `sudo systemd-tmpfiles --create vakr.conf` |
| `metadata_db` | `metadata.db` is missing, older, or newer than this release | `sudo vakr db migrate`, or the [database guide](database.md) |
| `auth_key` | `/var/lib/vakr/auth.key` is readable by anyone but `vakr`, or damaged | The message says how; see [users](users.md#where-the-secrets-live) |
| `listen` | `api.listen_address`:`api.port` is taken or not on this host | Free the port, or change `api.port` |
| `socket` | `/run/vakr` is missing, or another `vakr-api` answers on the socket | `sudo systemd-tmpfiles --create vakr.conf`, or stop the other instance |

A missing or unreadable telemetry database is not a startup failure. The API serves inventory and current state, and every telemetry view says "telemetry unavailable" instead of showing data. Storage trouble never reads as a device being down.

The installation key `/var/lib/vakr/auth.key` is created on the first start if `vakr setup` has not made it already.

## Who may do what

There are two roles ([users](users.md)). Every endpoint checks the caller's role itself (blueprint 3.1); hiding a button is never the only control.

| Caller | How it authenticates | Role |
|---|---|---|
| Anyone | nothing | Only `health/live`, `health/ready`, and `auth/login` |
| A signed-in user | the session cookie from `POST /api/v1/auth/login` | The account's role |
| A kiosk display | the kiosk cookie from `POST /api/v1/auth/kiosk`, or `Authorization: Bearer vkk_...` | operator, always |
| A process on the node | the Unix socket | By account: root and members of `vakr-admin` are admin, members of `vakr-view` and `vakr` are operator, anyone else is refused on every endpoint |

Operators read everything the API offers. Admins can also change things; in this release that is the identity-review decision. An anonymous request to a protected endpoint gets 401, and a role that lacks the permission gets 403. Both use the error envelope below.

## The local socket

Programs on the monitoring node talk to `/run/vakr/api.sock` without a password. The kernel reports which account connected, and the role follows from it (Appendix E.12). Group membership is the connecting process's own, the same rule the kernel uses for files: after `sudo usermod -aG vakr-admin alice`, Alice must log in again before it counts.

The socket file is mode 0666 inside `/run/vakr` (mode 0751), so any account on the node can reach it by name. That is deliberate: members of `vakr-admin` and `vakr-view` need not also be in `vakr`, which can also read the SNMP credentials. The API itself refuses every account that is neither root nor in one of the three groups, with 403 `peer_refused`, before it reads any data. This is how PostgreSQL's peer authentication works too.

No cookie, token, or CSRF header is used on the socket, and the sign-in endpoints answer 400 `local_socket` there.

`vakr status` and `vakr tui` read through the socket whenever it exists, and say so (`data: vakr-api socket`). With no socket file they read the databases directly; with a socket nothing answers on, they stop with exit status 3 rather than fall back ([status and TUI](status-and-tui.md#where-the-data-comes-from)). While `vakr-api` is stopped, `--local` reads the databases on purpose.

```console
$ sudo curl -s --unix-socket /run/vakr/api.sock http://localhost/api/v1/auth/session
{"kind":"local","name":"local:root","role":"admin","csrf_token":null,...}
```

## Signing in over HTTP

`POST /api/v1/auth/login` takes a JSON body `{"username": "...", "password": "..."}` (only `application/json`; anything else is 415). On success it answers the session and sets the session cookie:

| Cookie property | Value | Why |
|---|---|---|
| Name | `__Host-vakr_session` over HTTPS; `vakr_session` over plain HTTP from loopback | The `__Host-` prefix makes browsers refuse the cookie over HTTP and from other subdomains |
| `HttpOnly` | always | Page scripts can never read the token |
| `Secure` | over HTTPS | The cookie never travels unencrypted |
| `SameSite` | `Lax` | Links into vakr from tickets and chat keep the session; cross-site posts, frames, and fetches never carry it |
| `Path` / `Max-Age` | `/` and the session's remaining absolute lifetime (12 h) | The browser never keeps it longer than the server accepts it |

Which scheme counts is the one the client used to reach the reverse proxy, as the proxy reports it in `X-Forwarded-Proto` or `Forwarded`. Those headers are believed only from addresses listed in `api.trusted_proxies` ([web access](web-access.md)).

- **Plain HTTP is refused beyond loopback.** A sign-in over plain HTTP from any address other than loopback is refused with 403 `insecure_transport`, and no cookie is set. Passwords and session cookies cross the network only inside TLS.
- **Local testing.** Over plain HTTP from loopback, for example `http://127.0.0.1:8080` on the node itself, sign-in works and the cookie is `vakr_session` without `Secure`, because browsers drop secure cookies on `http://` URLs. Use this for testing only; real users come through the proxy.
- **Every sign-in starts a new session** and ends the one the browser presented. A failed sign-in is answered 401 `login_failed`, the same for a wrong password, an unknown account, and a disabled account. From the fifth failure in a row, for that account from that address, the answer is delayed (1, 2, 4, ... up to 30 s), and an attempt made before the wait is over is answered 429 with `Retry-After` (Appendix E.12). At most 8 sign-ins are checked at once and 32 more may wait; beyond that the answer is 429 at once.
- **CSRF.** Every state-changing request (`POST`, and later `PATCH` and `DELETE`) that a session cookie authenticates must send the session's token in the `X-CSRF-Token` header. Read it from `GET /api/v1/auth/session` or the sign-in answer. A missing or wrong token is 403 `csrf_failed`. A browser request that reports itself cross-site (`Sec-Fetch-Site`) or names another `Origin` than the `Host` it was sent to is 403 `cross_site`, token or not. Reads never change anything, so they need no token.
- **Sign out** with `POST /api/v1/auth/logout` (CSRF token required). It ends the session and clears the cookie.
- **CORS is off.** No answer carries `Access-Control-Allow-*`, so pages on other origins cannot read the API.

Sign-ins, failed sign-ins, and sign-outs are audited with the client address and the request ID, never the password ([users](users.md#audit-records)).

## Conventions every endpoint follows

- **Prefix** `/api/v1`. An incompatible change gets a new major version (API-004).
- **JSON** answers; times are UTC in ISO 8601 with `Z`. Identifiers are UUIDs, never database row numbers (API-002).
- **Missing is `null`, never 0** (invariant 1). Graph points without data are `null`, and stretches without data are `gap` markers.
- **Positions are never merged.** Each series in a graph is one collector's evidence (`collector_id`, `position`); latency answers list each position separately (MPOS-008).
- **Telemetry trouble is a state, not an error.** A view that cannot read telemetry answers 200 with `available: false` and a `reason`; device lists say "Unknown — telemetry unavailable" and never "Down".
- **Errors** use one envelope:

  ```json
  {"code": "invalid_request", "message": "the request is not valid",
   "request_id": "5f2c0d9e8a7b4c1d9e0f1a2b3c4d5e6f",
   "fields": [{"field": "limit", "message": "Input should be less than or equal to 500"}]}
  ```

  | Status | `code` | When |
  |---|---|---|
  | 400 | `local_socket` | Sign-in or sign-out on the socket |
  | 401 | `unauthenticated`, `login_failed` | No or invalid credentials; failed sign-in |
  | 403 | `forbidden`, `csrf_failed`, `cross_site`, `insecure_transport`, `peer_refused` | Role, CSRF, browser origin, plain HTTP, socket account |
  | 404 | `not_found` | Unknown endpoint or UUID |
  | 405 | `method_not_allowed` | Wrong method |
  | 409 | `conflict` | The decision is not possible (the review is closed, or the interface is on another device) |
  | 413 | `body_too_large` | Body over `api.max_body_bytes` |
  | 415 | `unsupported_media_type` | A sign-in that is not JSON |
  | 422 | `invalid_request` | A parameter out of bounds; `fields` names it |
  | 429 | `throttled`, `too_many_requests` | Sign-in throttling; see `Retry-After` |
  | 500 | `internal_error` | A failure inside vakr; the log names the request ID |
  | 503 | `storage_unavailable`, `busy`, `unavailable` | The metadata database cannot be read; password checks are saturated |

  Every answer, error or not, carries `X-Request-ID`. A trusted proxy may send its own `X-Request-ID` (1 to 64 letters, digits, `.`, `_`, `-`), and vakr then uses it.
- **Lists** take `limit` (1 to 500, default 100) and `cursor`, and answer `page`: `{"limit", "total", "next_cursor"}`. Pass `next_cursor` back as `cursor` and change nothing else; it is `null` on the last page (API-005). Cursors are opaque and at most 1,024 characters. Sites, tags, groups, collectors, identity history, and reviews use the store's keyset cursors, so a record added while you page never repeats or skips a row. Device and interface lists are ordered by live status or traffic, so their cursor is a position in a freshly computed order: a device whose status changes between two page requests can move across the page boundary.
- **Headers** on every answer: `Content-Security-Policy` (same origin only, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`, a restrictive `Permissions-Policy`, and, under `/api/`, `Cache-Control: no-store`. The web UI's pages add the SHA-256 hash of their bootstrap script to `script-src` and are `Cache-Control: no-cache`; its hashed files are cacheable for a year ([web UI](web-ui.md), Appendix E.31).

## Endpoints

"Operator" means operator or admin. The OpenAPI document has every parameter and answer shape.

| Method and path | Role | Answer |
|---|---|---|
| `GET /api/v1/health/live` | anyone | `{"status": "ok"}`; touches no database |
| `GET /api/v1/health/ready` | anyone | `ready`, `degraded` (telemetry unavailable), or 503 `not_ready`; component names and states only |
| `GET /api/v1/health/detail` | operator | Components with reasons, the release, the start time, every collector's self-report |
| `GET /api/v1/version` | operator | `{"version": "<release>", "api": "v1"}` |
| `POST /api/v1/auth/login` | anyone | The session (`kind`, `name`, `role`, `csrf_token`, `expires_at`, `idle_timeout_s`); sets the cookie |
| `POST /api/v1/auth/logout` | a session | 204; clears the cookie |
| `GET /api/v1/auth/session` | any caller | Who you are, your role, and for users the CSRF token and expiry; for a kiosk display the token's expiry |
| `POST /api/v1/auth/kiosk` | anyone | Sets a kiosk display up: `{"token": "vkk_..."}` as JSON, exchanged for an `HttpOnly`, `SameSite=Strict` kiosk cookie (`__Host-vakr_kiosk` over HTTPS). HTTPS only beyond loopback; a refused token is 401 `kiosk_refused` after 1 s. A session cookie in the same browser wins over the kiosk cookie |
| `POST /api/v1/auth/kiosk/forget` | anyone | 204; clears this browser's kiosk cookie (the token stays valid elsewhere) |
| `GET /api/v1/kiosk-tokens` | admin | Kiosk token records by label (label, creator, expiry, last use, `live`), never the tokens; paged |
| `POST /api/v1/kiosk-tokens` | admin | `{"label": "noc-wall", "lifetime": "90d"}` (lifetime optional, 1 to 365 days): 201 with the record and the token, which no other answer ever holds; 409 for a taken label |
| `DELETE /api/v1/kiosk-tokens/{kiosk_uuid}` | admin | 204; revokes the token, and every display holding it is refused from its next request |
| `GET /api/v1/overview` | operator | The overview dashboard: device counts, health, high-priority and WAN rows, top utilization and errors, event feeds, filter choices (`window`) |
| `GET /api/v1/devices` | operator | The device list in Attention, Active, Offline order, with counts and health; `search`, `bucket` (repeatable), `freshness` (repeatable: `fresh`, `stale`, `never`), `collector` (devices that position has evidence for), `sort` (`status`, `name`, `site`, `age`), `include_retired`, and `site`, `tag`, `group`, `provider`, `admin_state` to browse by grouping; paged |
| `GET /api/v1/devices/{device_uuid}` | operator | The device screen: facts, every position's verdict, warnings, the interface table |
| `GET /api/v1/device-lookup?key=` | operator | The same device screen for an exact display name, else a device UUID, as `vakr status DEVICE` takes it; `key` is 1 to 1,024 characters; 404 `not_found` when nothing matches |
| `GET /api/v1/devices/{device_uuid}/interfaces` | operator | The device's interfaces, sticky, WAN, and high-priority first; `search`, `sort`, `interest` (`primary` or `secondary`), `collector`; paged |
| `GET /api/v1/devices/{device_uuid}/history/reachability` | operator | Latency, loss, jitter, and the per-probe smoke, one set per position (graph parameters below) |
| `GET /api/v1/interfaces` | operator | Every interface; `search`, `site`, `tag`, `group`, `provider`, `wan`, `interest`, `lifecycle`, `include_retired`, `sort` (`priority`, `name`, `traffic`, `utilization`), `collector`; paged |
| `GET /api/v1/interfaces/{interface_uuid}` | operator | Identity and annotations (names, `ifIndex`, speeds, addresses, lifecycle, provider, circuit, contracted rate) plus current values from one position |
| `GET /api/v1/interfaces/{interface_uuid}/history/traffic` | operator | Inbound and outbound bit/s per position, with capacity and contracted-rate lines |
| `GET /api/v1/interfaces/{interface_uuid}/history/packets` | operator | Packets per second |
| `GET /api/v1/interfaces/{interface_uuid}/history/errors` | operator | Errors per second |
| `GET /api/v1/interfaces/{interface_uuid}/history/utilization` | operator | Percent of capacity; `available: false` for tunnels and other interfaces without a denominator |
| `GET /api/v1/interfaces/{interface_uuid}/history/status` | operator | Admin and oper state as segments, last 7 days of the range |
| `GET /api/v1/interfaces/{interface_uuid}/identity-history` | operator | Every identity decision, oldest first; paged |
| `POST /api/v1/interfaces/{interface_uuid}/identity-resolution` | admin | Answers an identity review (below) |
| `POST /api/v1/devices/{device_uuid}/rediscovery` | admin | A manual rediscovery (below): 202 with the queued operation and `Location`, or 200 with the one already queued or running; 409 `refused` with the reason |
| `GET /api/v1/operations` | operator | Admin operations newest first; `device`, `state` (repeatable); paged |
| `GET /api/v1/operations/{operation_uuid}` | operator | One operation: state, requester, times, collector, and the result or the reason |
| `GET /api/v1/identity-reviews` | operator | Open reviews with candidates, oldest first; `device`; paged |
| `GET /api/v1/sites`, `/tags`, `/groups` | operator | Name-ordered pages; their UUIDs are the `site`, `tag`, `group` filters |
| `GET /api/v1/collectors` | operator | Each polling position with its latest health report and spool level; paged |
| `GET /api/v1/collectors/{collector_uuid}/health` | operator | One collector |
| `GET /api/v1/rankings/utilization` | operator | Top N by the higher directional p95 utilization: `window` (`15m`, `1h`, `6h`, `24h`), `limit` (1 to 100), `collector`, and the interface filters; with coverage and excluded interfaces |
| `GET /api/v1/rankings/errors` | operator | Top N by valid error deltas, with errors per million packets when known |
| `GET /api/v1/high-priority` | operator | Primary-interest interfaces in priority order; paged |
| `GET /api/v1/wan-circuits` | operator | WAN circuits in name order with provider, circuit, contracted rate; interface filters; paged (500 per page by default) |
| `GET /api/v1/search?q=` | operator | Plain substring search over devices, interfaces, sites, providers, circuits, tags, groups; `per_kind` (1 to 500, default 50). Interface hits carry `interface_id` |
| `GET /api/v1/exports/interfaces/{interface_uuid}/history/{kind}` | operator | The graph as a CSV or JSON file (`format`); same parameters as the graph |
| `GET /api/v1/exports/devices/{device_uuid}/history/reachability` | operator | Latency and loss as a file |
| `GET /api/v1/exports/rankings/{utilization\|errors}` | operator | A ranking as a file |

**Graph parameters.** `range` is a preset: `1h`, `6h`, `24h` (the default), `7d`, `30d`, `90d`, or `400d`. Or give `start` and `end` in ISO 8601 with a timezone; a custom range is 1 minute to 400 days and may end at most 5 minutes in the future. `source` is `auto` (the default: rollups beyond 30 days), `native`, or `rollup`. `max_points` is 1 to 1,200 per series. `collector` asks for one position. A range out of bounds is refused with 422; nothing is silently widened (API-006). Each series says what it rests on: `resolutions` (the source and bucket width of each part of the window), `gaps`, `markers` (restart, discontinuity, baseline, partial, collector, over capacity, gap), and `truncated`. `window.timezone` is the display zone and `window.site_timezone` the subject's site zone. The [query services guide](../architecture/queries.md) explains the source choice and the markers.

**Exports** answer `Content-Disposition: attachment`. CSV has one row per point with `time_utc`, the position, the value, its unit, source, bucket width, the display timezone, and flags; a missing value is an empty cell. An export over 20,000 rows or 4 MiB is refused with 422 (API-012). Exports are synchronous within those bounds.

**Identity resolution.** When discovery cannot tell which existing interface a row is, it opens a review ([inventory](inventory.md)). An admin answers it with `POST /api/v1/interfaces/{interface_uuid}/identity-resolution` and `{"review_id": "...", "decision": "map"}` (the row is this interface; its history continues) or `"decision": "new"` (the row is a different interface and gets its own identity; the interface in the path must be on the review's device). It is the same decision as `vakr interface review map|new`: one transaction updates identities, closes the review, writes identity history and an audit record that name the admin. A closed review or an interface on another device is 409.

**Manual rediscovery.** `POST /api/v1/devices/{device_uuid}/rediscovery` (no body) answers at once and never waits for SNMP (DISC-010): 202 with an operation in state `queued` and `Location: /api/v1/operations/{id}`. Follow that address: `running` once a collector claimed it, then `succeeded` with `result` (`interfaces`, `new`, `renumbered`, `missing`, `reviews_opened`, `capability_changes`, `partial`, `duration_ms`), or `failed` or `expired` with `error`. A request for a device that already has one queued or running answers 200 with that operation. A device no collector would discover (disabled, retired, no SNMP credential, `icmp_only`) and a full queue (1,000 active operations) answer 409 `refused` with the reason. Each request is audited with the admin's name; the bounds are in Appendix E.32 of the blueprint. `vakr inventory rediscover` does the same on the node ([inventory](inventory.md#rediscovering-a-device-now)).

## Not in this release

These parts of the blueprint's API (section 14) come in later releases: creating and editing devices and interfaces (`POST /devices`, `PATCH`), inventory imports, policies, dashboards and their publication, events, asynchronous exports (`/exports/{operation_uuid}`), the server-sent event stream, and public dashboards (`/public/v1`). Until then, inventory changes are made in `/etc/vakr/inventory.toml`.

## OpenAPI document

The machine-readable description of every endpoint, parameter, and answer is checked in as [`docs/api/openapi.json`](../api/openapi.json) (OpenAPI 3.1, API-008). It is generated from the code; a test fails when it is out of date, so it always matches the release. The running API serves the same document at `GET /api/v1/openapi.json`. Interactive documentation pages are not served, because they would load scripts from a CDN (UI-012); open the file in any OpenAPI viewer instead.

```console
$ vakr api openapi > openapi.json                     # print it
$ make openapi                                       # developers: regenerate docs/api/openapi.json
```

`info.version` in the document is the API version, `1`, not the vakr release.

## Health and monitoring the API

- `GET /api/v1/health/live` says the process answers. Use it for liveness probes.
- `GET /api/v1/health/ready` says whether the API can serve: 200 `ready`, 200 `degraded` (telemetry unavailable, so graphs are empty but inventory works), or 503 `not_ready` (metadata unavailable).
- `vakr check health` and `vakr doctor` include an `api` check that asks `health/ready` over the socket. It is OK when the API is ready, and also when it is not installed or not running (the unit ships disabled). It is CRITICAL when the socket exists but nothing answers within 2 seconds, or the API is not ready, and UNKNOWN when the caller may not use the socket. Run it as root or as a member of `vakr`. The API never runs this check itself.

## Limits

| Limit | Value |
|---|---|
| Request body | `api.max_body_bytes` (64 KiB) |
| Request head (request line and headers) | 16 KiB |
| Connections plus requests in progress | 100 on TCP, 32 on the socket; more get 503 |
| Idle keep-alive | 5 s |
| Page size | 1 to 500, default 100 |
| Points per series | 1 to 1,200 |
| Graph range | 1 minute to 400 days |
| Ranking window and rows | 15 minutes to 24 hours; 1 to 100 rows |
| Search text | 256 characters, plain text |
| Export | 20,000 rows and 4 MiB |
| Expired sessions purged | every 5 minutes, at most 5,000 rows per table per pass |

All of them are in [Appendix E.30](../../tool-blueprint.md#e30-api-service-m4) with their reasons. The options are in the [configuration reference](configuration.md) under `[api]`.

## Examples

On the node, as root or a member of `vakr-view` or `vakr`, over the socket:

```console
$ curl -s --unix-socket /run/vakr/api.sock http://localhost/api/v1/health/ready
{"status":"ready","checks":[{"name":"metadata","status":"ok",...}],...}
$ curl -s --unix-socket /run/vakr/api.sock 'http://localhost/api/v1/devices?bucket=attention&limit=20'
$ curl -s --unix-socket /run/vakr/api.sock \
    'http://localhost/api/v1/interfaces/0190a3c4-0000-7000-8000-00000000002a/history/traffic?range=7d'
$ curl -s --unix-socket /run/vakr/api.sock -o top.csv \
    'http://localhost/api/v1/exports/rankings/utilization?window=1h&limit=25'
```

Through the reverse proxy, signing in with a cookie jar and sending the CSRF token on a change (`jq` reads the token from the JSON answer):

```console
$ read -rs PW
$ curl -s -c jar -H 'Content-Type: application/json' \
    --data-binary @- https://vakr.example.net/api/v1/auth/login <<EOF
{"username": "alice", "password": "$PW"}
EOF
$ token=$(curl -s -b jar https://vakr.example.net/api/v1/auth/session | jq -r .csrf_token)
$ curl -s -b jar -H "X-CSRF-Token: $token" -H 'Content-Type: application/json' \
    -d '{"review_id": "0190a3c4-0000-7000-8000-000000000301", "decision": "map"}' \
    https://vakr.example.net/api/v1/interfaces/0190a3c4-0000-7000-8000-00000000002a/identity-resolution
$ curl -s -b jar -H "X-CSRF-Token: $token" -X POST https://vakr.example.net/api/v1/auth/logout
```

The password goes in on standard input, so it does not appear in the process list or shell history.

A program that is not a browser reads with a kiosk token ([kiosk displays](kiosk.md)) as a bearer token:

```console
$ curl -s -H "Authorization: Bearer $KIOSK_TOKEN" https://vakr.example.net/api/v1/overview
```
