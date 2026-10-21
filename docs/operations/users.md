# Users, sessions, and sign-in

vakr's web UI and API sign people in with **local accounts** stored in the metadata database. There are two roles:

| Role | May do |
|---|---|
| **admin** | Everything an operator can, plus manage accounts, inventory, policies, dashboards, kiosk tokens, rediscovery, and identity reviews. |
| **operator** | Read monitoring state, graphs, dashboards, inventory, and collector health. Nothing that changes configuration (SEC-006). |

Permissions are checked in the application, not just by hiding buttons (blueprint 3.1). Accounts sign in to the JSON API of the `vakr-api` service ([API](api.md)); the web pages built on it arrive in a later release.

- [First run: `vakr setup`](#first-run-vakr-setup)
- [Managing accounts: `vakr user`](#managing-accounts-vakr-user)
- [Passwords](#passwords)
- [Sessions](#sessions)
- [Failed logins and lockout](#failed-logins-and-lockout)
- [Kiosk displays](#kiosk-displays)
- [Where the secrets live](#where-the-secrets-live)
- [Local TUI and CLI access](#local-tui-and-cli-access)
- [Audit records](#audit-records)

## First run: `vakr setup`

A new installation has no accounts. Create the first admin on the monitoring node:

```sh
sudo vakr setup
```

`vakr setup` asks for a username (default `admin`) and then for a password twice. Nothing echoes as you type. It also creates the installation key `/var/lib/vakr/auth.key` (see [Where the secrets live](#where-the-secrets-live)) and finishes with the remaining first-run steps: credentials, inventory, and the collector.

| Option | Meaning |
|---|---|
| `--username NAME` | Skip the username question. |
| `--display-name TEXT` | A longer name for this account. API v1 does not return it yet, so the web UI shows the username. |
| `--add-admin` | Create another admin even though one exists. |

Once an enabled admin exists, `vakr setup` refuses to run again, so a second run cannot quietly create an administrator. Use `vakr user add` for more accounts, or pass `--add-admin` if another admin really is what you want.

Like `vakr inventory`, the command switches from root to the `vakr` service user before it opens anything, so the database and key file stay owned by the services. It needs the metadata database, which the package creates on installation. If the database is missing, `vakr setup` exits with status 4 and asks you to run `sudo vakr db migrate`.

Exit status: 0 created; 1 refused (an admin exists, or no acceptable password was entered after three tries); 2 usage error; 4 the metadata database is unavailable.

## Managing accounts: `vakr user`

All `vakr user` commands are admin operations. Run them with `sudo` on the monitoring node.

| Command | What it does |
|---|---|
| `vakr user list [--json]` | Lists accounts with role, state, and last login (UTC). JSON output never includes a password hash. |
| `vakr user add NAME --role admin\|operator [--display-name TEXT]` | Creates an account. Asks for the password twice. |
| `vakr user passwd NAME` | Sets a new password. Asks twice. Ends all of that account's sessions. |
| `vakr user disable NAME` | Locks the account and ends its sessions. |
| `vakr user enable NAME` | Unlocks the account. |
| `vakr user role NAME admin\|operator` | Changes the role and ends the account's sessions, so the new role applies from the next sign-in. |
| `vakr user revoke-sessions NAME` | Signs the account out everywhere. |

Accounts are disabled, never deleted, so audit records always name a real account. The last enabled admin cannot be disabled or demoted. Make another account admin first.

Usernames are 1 to 64 characters: letters, digits, `.`, `_`, `@`, and `-`, starting with a letter or digit. They are not case-sensitive. vakr stores them in lower case, so `Alice` and `alice` are the same account.

**Passwords are never accepted as an argument or an environment variable.** Command lines show up in `ps` and shell history, and environments leak into child processes. When standard input is not a terminal, each password (and its repeat) is read as one line of standard input instead, so a script can pipe them in: `printf '%s\n%s\n' "$pw" "$pw" | sudo vakr user add bob --role operator`. Without a terminal, `vakr setup` does not ask for a username; give `--username`, or it uses `admin`.

## Passwords

Passwords are hashed with **Argon2id**, using the `argon2-cffi` defaults: the RFC 9106 low-memory profile, with 3 passes and 64 MiB. Each hash carries its own parameters. When a later release raises them, the next successful sign-in stores a fresh hash automatically (SEC-009). Nothing needs to be done by hand.

The rules for a new password follow NIST SP 800-63B:

- at least **15 characters** (`auth.password_min_length`), because the password is the only factor;
- at most 256 characters;
- any characters, including spaces and non-English letters, with no "must contain a digit" rules;
- not the username, not one character repeated, and no control characters.

Unicode text is normalised (NFKC) before hashing, so the same password typed in a terminal and in a browser always matches. Existing passwords keep working when the minimum length changes; the rule applies the next time a password is set.

At most four hashes are computed at once, since each needs 64 MiB. A login that cannot start one within 10 seconds fails as busy instead of piling up memory.

## Sessions

Signing in (`POST /api/v1/auth/login`, [API](api.md#signing-in-over-http)) creates a **server-side session**. The browser gets an opaque random token (256 bits) in a cookie. The server stores only its SHA-256 digest, so a copy of the database cannot be replayed as a login.

| Limit | Default | Option |
|---|---|---|
| Idle expiry | 30 minutes without a request | `auth.session_idle_timeout` |
| Absolute expiry | 12 hours after sign-in, however active | `auth.session_lifetime` |
| Sessions per account | 20; the 21st sign-in ends the oldest | fixed (E.12) |

Use is recorded at most once a minute, so a session can end up to a minute *earlier* than 30 minutes after its last request, never later. Signing in always starts a new session and ends the one the browser presented (session rotation, SEC-010). A password change, a role change, or disabling an account ends all of that account's sessions. Expired rows are removed in batches of at most 500 per table.

Every form or request that changes something must carry a **CSRF token** bound to the session (SEC-011). See [Where the secrets live](#where-the-secrets-live).

## Failed logins and lockout

vakr does not lock accounts. It slows guessing down instead, for each combination of attempted username **and** client address (SEC-012, Appendix E.12):

| Consecutive failures | Wait before the next attempt |
|---|---|
| 1–4 | none |
| 5 | 1 s |
| 6, 7, 8, 9 | 2, 4, 8, 16 s |
| 10 and more | 30 s (`auth.login_throttle_max_delay`) |

- The answer to a failed attempt is itself delayed by that wait. An attempt that arrives before the wait is over is refused straight away, without checking the password. A flood therefore costs neither CPU nor audit records.
- Because the count is per address, someone guessing Alice's password from one host does not lock Alice out from her own desk.
- A successful sign-in clears the count. So do 15 minutes without a new failure (`auth.login_failure_reset`).
- An unknown username is delayed exactly like a real one, and the reply is the same, so the delays never reveal which accounts exist (AUTH-004).
- Failure counts are stored under a keyed digest of the attempted username, never the text itself, because people sometimes type their password into the username field. At most 10,000 counts are kept; the quietest are dropped first.

The options `auth.login_throttle_after` (5), `auth.login_throttle_max_delay` (30 s), and `auth.login_failure_reset` (15 min) tune the schedule. See [configuration](configuration.md).

## Kiosk displays

A NOC wall screen cannot type a password every 30 minutes. An admin creates a **kiosk token** for it instead, labelled for the display, and enters it into the display's browser once. A kiosk token:

- has the **operator** role only, so it can view and never change anything;
- has no idle expiry, and expires 30 days after creation (`auth.kiosk_token_lifetime`);
- is shown once at creation and stored only as a digest;
- can be revoked at any time, with immediate effect.

At most 100 kiosk tokens can exist. The API accepts them as `Authorization: Bearer vkk_...`; creating and revoking them arrives with kiosk dashboards, after this release.

## Where the secrets live

| What | Where | Protection |
|---|---|---|
| Password hashes (Argon2id) | `local_user` table in `/var/lib/vakr/metadata.db` | Database file owned by `vakr`. Never logged, printed, or audited. |
| Session and kiosk tokens | Only in the browser. The server keeps SHA-256 digests in `auth_session` and `kiosk_token`. | A digest cannot be turned back into a token. |
| Installation key | `/var/lib/vakr/auth.key` (64 hex digits) | Mode 0600, owner `vakr`. vakr refuses to use it if anyone else can read it. |

The installation key is created by `vakr setup` and, once it exists, by `vakr-api` on first start if the file is missing. Two purpose-bound keys are derived from it with HMAC-SHA-256:

- the **CSRF key**: a session's CSRF token is `HMAC(CSRF key, session ID)`. Every page can include it without the server storing it, each new session gets a new one, and a token from one session is useless in another;
- the **throttle key**: failed-login counts are stored under `HMAC(throttle key, attempted username)`.

The key is not included in backups. If it is lost or deleted, vakr creates a new one. Open pages then need a reload to get a fresh CSRF token, and failure counts start from zero. Nothing else is affected.

Members of the `vakr` group can read `metadata.db` and therefore the password hashes, just as they can read the SNMP credentials file. Argon2id makes guessing expensive, but give people a web account or `vakr-view` membership ([local TUI and CLI access](#local-tui-and-cli-access)) rather than `vakr` membership if that matters at your site. Database backups made by `vakr db migrate`, `vakr db backup`, and self-update contain the hashes too. They are kept in directories with mode 0700.

## Local TUI and CLI access

On the monitoring node itself, `vakr status` and `vakr tui` talk to the API over the Unix socket `/run/vakr/api.sock` without a password. The kernel reports who connected (`SO_PEERCRED`), and the role follows from that account:

| Connecting account | Role |
|---|---|
| root, or a member of `vakr-admin` | admin |
| a member of `vakr-view` | operator |
| a member of `vakr` | operator |
| anyone else | refused |

Operators only read (SEC-006). Give people who only look `vakr-view`: it grants the socket and nothing else. `vakr` is the service group, which can also read the SNMP credentials file and `metadata.db`. A `vakr-view` member needs `vakr-api` running, because the group cannot read the databases directly.

Group membership is that of the *connecting process*, the same rule the kernel uses for file access. After `sudo usermod -aG vakr-admin alice`, Alice must log out and in again before the new group counts. Members of `vakr-admin` and `vakr-view` need not also be in `vakr`: the socket is reachable by every account (mode 0666 in `/run/vakr`, mode 0751), and `vakr-api` refuses everyone else itself. `vakr-api` serves the socket ([API](api.md#the-local-socket)). When it is not running, `vakr status` and `vakr tui` read the databases directly instead, as described in [status and TUI](status-and-tui.md#where-the-data-comes-from).

## Audit records

Every sign-in, failed sign-in, sign-out, and account change is recorded in the audit log in the same transaction as the change (SEC-018). Records name the account and the client address, never a password, hash, or token.

| Action | Recorded when |
|---|---|
| `auth.login` | A sign-in succeeds (`success`), fails (`failure`), or is refused because the account is disabled (`denied`). A failure for an unknown username is recorded with the actor `anonymous`, without the attempted name. |
| `auth.logout` | A user signs out. |
| `user.create`, `user.password`, `user.enable`, `user.disable`, `user.role`, `user.revoke_sessions` | An admin changes an account. |
| `kiosk.create`, `kiosk.revoke` | An admin creates or revokes a kiosk token. |

Changes made with `vakr setup` or `vakr user` name the operator who ran the command: `cli:alice (sudo)` when Alice used `sudo`, or `cli:<login>` otherwise. Attempts refused while throttled are not recorded individually, so a password-guessing flood cannot fill the audit log.
