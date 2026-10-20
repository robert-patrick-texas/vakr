# Credentials

vakr needs secrets to do its job: an SNMPv2c community (MVP), SNMPv3 user passphrases (after the MVP), and, on larger profiles, database connection strings. They live in one protected file, **`/etc/vakr/secrets/credentials.toml`**, and nowhere else (CONF-007, SEC-002). Device definitions in `config.toml` or `inventory.toml` only *name* a credential, for example `credential = "network-v2c"` (SEC-001), so you can rotate a community without touching a single device record.

vakr never shows a secret value: not in logs, error messages, tracebacks, `vakr` command output, health checks, the API, support bundles, or audit records (SEC-003). There is no command that prints one. To see a value, read the file with `sudoedit`.

## The file

```toml
# /etc/vakr/secrets/credentials.toml: mode 0640, owner root:vakr

[credentials.network-v2c]          # MVP: SNMPv2c
version = "2c"
community = "<community>"

[credentials.network-snmpv3]       # after the MVP: SNMPv3
version = "3"
username = "netmon"
security_level = "authPriv"
auth_protocol = "SHA256"
auth_secret = "<auth passphrase>"
privacy_protocol = "AES128"
privacy_secret = "<privacy passphrase>"

[secrets]                          # named strings, such as database DSNs
metadata-dsn = "<dsn>"
telemetry-dsn = "<dsn>"
```

Everything in angle brackets is a placeholder. Never commit the real file, and never paste it into a ticket or chat.

Each `[credentials.NAME]` table is one SNMP credential, and `NAME` is what device records refer to. A name starts with a letter or digit and uses only letters, digits, `.`, `_` and `-`. The `[secrets]` table holds named strings. Settings such as `dsn_secret = "metadata-dsn"` in `config.toml` refer to them by name.

| Field | Versions | Values |
|---|---|---|
| `version` | both | `"2c"` or `"3"` (strings, with quotes) |
| `community` | 2c | the community string; required |
| `username` | 3 | USM user name; required |
| `security_level` | 3 | `noAuthNoPriv`, `authNoPriv`, or `authPriv`; required |
| `auth_protocol`, `auth_secret` | 3 | `MD5`, `SHA` (SHA-1), `SHA224`, `SHA256`, `SHA384`, or `SHA512`, and a passphrase of at least 8 characters; required for `authNoPriv` and `authPriv`, not allowed for `noAuthNoPriv` |
| `privacy_protocol`, `privacy_secret` | 3 | `DES`, `AES128`, `AES192`, or `AES256`, and a passphrase of at least 8 characters; required for `authPriv`, not allowed otherwise |

Any other key is an error. An error message names the file, the line, and the key, never the value. A TOML syntax error on a line that holds a secret reports only the line number and a fixed description such as `TOML syntax error: unterminated string`.

## Permissions and ownership

The package creates `/etc/vakr/secrets/` with mode `0750`, owned by `root:vakr`. The vakr services run as the `vakr` user and read the file through the `vakr` group.

vakr checks the file every time it reads it. In strict mode, which is what every service uses, it refuses a file that:

- is a symbolic link or anything other than a regular file;
- is not owned by `root` or `vakr`;
- is writable by its group or by others;
- is readable by others;
- is readable by a group other than `vakr`;
- sits in a directory that is writable by its group or by others, or that is not owned by `root` or `vakr`.

The expected setup is mode `0640`, owner `root:vakr`. Mode `0600` owned by `vakr` also works.

```sh
sudo chown root:vakr /etc/vakr/secrets/credentials.toml
sudo chmod 0640 /etc/vakr/secrets/credentials.toml
```

`vakr credentials check --dev` relaxes the permission rules to warnings, for a developer workstation where you can't create root-owned files. It is a command-line flag only. No environment variable or setting turns strict mode off for the services.

## Setting the SNMPv2c community

Edit the file with an editor. **Don't put the community on a command line.** Commands like `echo 'community = "..."' | sudo tee -a ...` end up in your shell history, in `ps` output while they run, and in the sudo log in the journal.

1. Create the file with the right owner and mode, if it doesn't exist yet:

   ```sh
   sudo install -m 0640 -o root -g vakr /dev/null /etc/vakr/secrets/credentials.toml
   ```

2. Edit it with `sudoedit`, which works on a private copy and writes it back without changing the owner or mode:

   ```sh
   sudoedit /etc/vakr/secrets/credentials.toml
   ```

   Add the table and type or paste the community between the quotes:

   ```toml
   [credentials.network-v2c]
   version = "2c"
   community = "<community>"
   ```

3. Check the result:

   ```sh
   sudo vakr credentials check
   ```

If you edit the file directly as root instead, make sure your editor doesn't leave a backup copy such as `credentials.toml~` in the secrets directory.

## Checking and listing

| Command | What it does |
|---|---|
| `vakr credentials check [--dev]` | Checks permissions, TOML syntax, every field, and that each SNMPv3 credential's level, protocols, and secrets fit together. Prints names, kinds, versions, and security levels, plus a note for every credential that isn't the recommended SNMPv3 posture. |
| `vakr credentials list [--dev]` | Prints the names, kinds, and versions the file defines. |

Both commands exit with `0` when the file is valid, `1` when they find problems, and `2` when the file is missing or can't be read. Run them as root or as a member of the `vakr` group.

`vakr check health` and `vakr doctor` include a `credentials` check that reads only local files:

| Result | Meaning |
|---|---|
| OK | The file passes strict checks. It also reports OK on a host without `/etc/vakr/secrets`, which is not an installed vakr node. |
| WARNING | The file doesn't exist yet, which is normal on a fresh install before you add credentials. |
| UNKNOWN | The user running the check can't read the file. |
| CRITICAL | Unsafe permissions or an invalid file. Run `vakr credentials check` for the details. |

## Rotation

Credential names stay the same when values change, so device records never need editing (blueprint 15.2). Before every use, vakr compares the file's inode, size, and modification and change times with the version it loaded. On any change it drops the old values and reads the file again, so the next poll uses the new value. You don't need to restart anything.

To rotate a community on Cisco IOS XE:

1. Add the new community on the devices alongside the old one.
2. Change the value in `credentials.toml` with `sudoedit`, or write a complete new file in the same directory with the same owner and mode and `mv` it over the old one, which replaces it atomically.
3. Run `sudo vakr credentials check`, then watch the next polling cycle succeed.
4. Remove the old community from the devices.

If the edited file is invalid or unsafe, vakr stops using the old values as well. Polls that need a credential fail with a credential error until you fix the file, and the `credentials` health check reports CRITICAL. vakr never keeps polling with a secret you meant to replace.

## What is never logged

- **Values are wrapped as soon as they are read.** Every printed or logged form of a credential shows `Secret('***')`. Secret values can't be pickled, written as JSON, or compared with plain strings.
- **Errors name only the location.** File errors give the path, line, key, and rule, never the value, and the parser's own error, which holds the whole file, is discarded.
- **Logs are scrubbed.** A logging filter replaces any known secret value in log messages, arguments, and exception text with `***`, as a second line of defence.
- **Values stay in memory only while needed.** A service holds them only while the file is unchanged, resolves a credential immediately before opening an SNMP session, and doesn't keep it afterwards.
- **Backups contain secrets.** The pre-update backup under `/var/lib/vakr/backups/` (mode `0600`) includes `/etc/vakr/secrets/`; see [Updates](updates.md).

## Recommended: SNMPv3 authPriv with SHA-2 and AES

SNMPv2c sends the community in clear text in every packet, so anyone who can capture traffic on the management path can read it. Until you move to SNMPv3, restrict SNMP on each device to the vakr pollers with an access list, and use a read-only community.

SNMPv3 support follows the MVP. When it arrives, use `security_level = "authPriv"` with a SHA-2 authentication protocol (`SHA256` or stronger) and AES privacy (`AES128` or stronger), wherever your devices support them (SNMP-003). `vakr credentials check` notes every credential that falls short of this, including every SNMPv2c credential. MD5, SHA-1, and DES are accepted for older devices but aren't recommended.

See also: [installation](install.md) for the directory layout, and [configuration](configuration.md) for the settings that refer to credentials.
