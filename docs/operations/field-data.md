# Field data for the developers

vakr is developed away from the site where it runs ([blueprint 28.1](../../tool-blueprint.md#281-separating-development-from-production)). When the developers need facts from production (what the monitoring host looks like, how the real routers answer SNMP, the latency between the east and west positions, or what vakr itself reports), you run **one command** that produces **one encrypted file**, and you send that file back. That command is the field kit, `vakr-fieldkit` ([FIELD-001..010](../../tool-blueprint.md#285-field-data-from-production)).

The field kit only reads. It changes nothing on the host apart from writing the bundle into its output directory and, if you ask for pseudonymization, keeping one small key file. It never changes anything on a device: it only sends SNMP GET and GETBULK requests.

## Run it

### Before vakr is installed

Download `vakr-fieldkit.pyz` from the newest release, the same way the [install guide](install.md#1-download-a-release) finds it. The release's `SHA256SUMS` lists the kit, so you can verify it exactly as in [step 2 of the install guide](install.md#2-verify-the-download) before you run it.

```sh
repo=robert-patrick-texas/vakr
version=$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=1" |
    sed -n 's/^ *"tag_name": *"v\([^"]*\)".*/\1/p')
base=https://github.com/$repo/releases/download/v${version:?not found}
curl -fL -O "$base/vakr-fieldkit.pyz" -O "$base/SHA256SUMS" -O "$base/SHA256SUMS.sig"
```

Then run it with [uv](https://docs.astral.sh/uv/). uv fetches Python 3.15 itself, so the system Python doesn't matter.

```sh
uv run --python 3.15 vakr-fieldkit.pyz collect \
    --profile host,snmp,paths \
    --devices devices.txt --community-file ~/.vakr-community \
    --peer vakr-west.example.net --pseudonymize
```

### After vakr is installed

The package ships the same tool at `/opt/vakr/share/fieldkit/vakr-fieldkit.pyz`, and `vakr field collect` runs it with vakr's bundled Python. It accepts exactly the same options, and the `vakr` profile is now useful:

```sh
vakr field collect \
    --profile host,snmp,paths,vakr \
    --devices devices.txt --community-file ~/.vakr-community \
    --peer vakr-west.example.net --pseudonymize
```

`vakr field collect --help` shows every option. To try a newer kit from a release without upgrading vakr, set `VAKR_FIELDKIT=/path/to/vakr-fieldkit.pyz`.

### What you see

Progress goes to standard error. The run always ends with exactly one line on standard output that names the file and the next step:

```text
vakr-fieldkit: collecting profile host
vakr-fieldkit: collecting profile snmp
vakr-fieldkit: collecting profile paths
vakr-fieldkit: wrote ./vakr-field-host-3fa2c1d0-20261020T101500Z.tar.gz.p7m; next: scp it to ~/vakr-inbox/ on the development host or put it at an HTTPS URL and send the link
```

If some steps failed (for example, one router didn't answer), the line says how many failures `manifest.json` lists. Send the bundle anyway, because the failures are useful information too.

| Exit status | Meaning |
|---|---|
| 0 | A bundle was written. |
| 1 | Collection or encryption failed. If encryption failed, the line names the unencrypted bundle, which never left this host. |
| 2 | A usage or preflight problem. Nothing was collected. |
| 130 | Interrupted with Ctrl-C. The partial bundle was removed. |

## Prepare the inputs

**Device list** (`--devices`) has one device per line. `#` starts a comment. Entries are host names, addresses, or net-snmp agent specifications such as `10.0.0.1:1161` or `udp6:[2001:db8::1]:161`.

```text
# core
rtr-east-1.example.net
10.20.30.5
```

**SNMPv2c community.** There's deliberately no option that takes the community on the command line, because anything on a command line shows up in `ps` for every user on the host ([FIELD-004](../../tool-blueprint.md#285-field-data-from-production)). Put it in a file that only you can read:

```sh
install -m 600 /dev/null ~/.vakr-community
editor ~/.vakr-community          # one line: the community
```

The kit refuses the file if group or others can read it. If you leave out `--community-file`, it asks for the community with a hidden prompt. It uses the first line that is not empty and doesn't start with `#`. Communities with control characters, non-ASCII characters, leading or trailing blanks, or a leading `"` are refused, because net-snmp's configuration parser would change them.

The community is handed to net-snmp through an `snmp.conf` in a private temporary directory, and that directory is deleted when the run ends. It is never written into the bundle. If a device echoes it back, for example in `sysContact`, it is replaced with `[REDACTED]`, and the manifest notes that this happened.

## What is collected

| Profile | Collected | Needs |
|---|---|---|
| `host` | <ul><li>`/etc/os-release`, kernel, and CPU count and model</li><li>`/proc/meminfo`</li><li>`df`</li><li>time sync: `timedatectl show`, plus `chronyc tracking` if chrony is installed</li><li>`net.ipv4.ping_group_range`</li><li>the Python and `openssl version -a` versions</li><li>which CA bundles exist</li><li>proxy environment variables, with credentials redacted</li><li>the GitHub reachability test (below)</li></ul> | nothing |
| `snmp` | For each device:<ul><li>bounded `snmpbulkwalk` walks of the system group, `ifTable`, `ifXTable`, `ipAddressTable`, `ipAddrTable`, and any `--extra-oid` subtrees</li><li>`sysUpTime`, sampled twice at least `--uptime-gap` seconds apart, so the agent's tick rate can be checked</li></ul> | `--devices`, and the community |
| `paths` | `ping -c 20 -i 0.2` statistics (loss and min/avg/max/mdev RTT) to `--peer` and to the first `--ping-devices` devices | `--peer` or `--devices`, and the `ping` program (`iputils-ping`, which the vakr package recommends) |
| `vakr` | <ul><li>`vakr --version`, `vakr doctor`, `vakr config show-effective`, `vakr check health`</li><li>`vakr acceptance schedule`, `gaps`, and `coverage`, each with `--json` ([acceptance reports](acceptance.md))</li><li>the last `--journal-lines` lines of `journalctl -u 'vakr*'` from the past 24 hours</li></ul>Commands that this release hasn't built yet are skipped. | vakr installed |

**Not collected:**
- device configurations;
- any credential;
- packet captures;
- files outside the list above.

Every external program is started with an argument list and never through a shell.

**Load on devices.** Devices are walked one at a time, with one request in flight, 10 repetitions per GETBULK, a pause of `--device-pause` seconds between devices, and a timeout per walk. A run of 100 devices normally takes a few minutes, and `--time-budget` caps the whole run.

### GitHub reachability test

The `host` profile checks whether this host could reach GitHub for vakr's self-update ([FIELD-010](../../tool-blueprint.md#285-field-data-from-production)). It sends one GET to the release API and one HEAD to the releases page, and it records each stage separately: DNS, the TCP connect, the proxy `CONNECT`, the TLS handshake, and HTTP.

- **Proxy.** Use `--proxy http://[user:pass@]proxy:port`, or the usual `HTTPS_PROXY` and `NO_PROXY` variables. Recorded URLs never contain the credentials.
- **Enterprise TLS inspection.** If your proxy re-signs TLS with its own CA, pass that CA with `--ca-file inspection-ca.pem`. The trust store is the OS bundle plus that file. Certificate verification is never disabled for the request itself.
- **When verification fails**, the report names the issuer and subject of the certificate this host was actually shown, which tells the developers which CA vakr needs (`vakr tls trust-ca`). Learning this takes one extra TLS handshake without verification. That connection is closed right after the handshake, and nothing is sent over it.
- **Strict X.509 profile.** The test checks certificates the way vakr does, with the strict X.509 profile that Python 3.13 and later enforce. Some inspection CAs, and the certificates their proxies issue, fail that profile even when the CA is trusted. Typical reasons are `Missing Authority Key Identifier` and `CA cert does not include key usage extension`. When that happens, the test makes one more handshake with only the strict profile relaxed. That handshake still verifies the certificate and the host name, and it sends nothing. If it succeeds, the report says `only the strict X.509 profile rejects it`, suggests `--no-strict-x509` for comparison, and names vakr's fallback setting, `network.tls_strict_x509 = false`. The real fix is a reissued CA. See [The strict X.509 profile](updates.md#the-strict-x509-profile).
- `--no-strict-x509` runs the test with only the strict profile relaxed, as vakr does with `network.tls_strict_x509 = false`. Certificate and host-name verification stay on.
- `--no-egress` skips the test.

## Pseudonymization

With `--pseudonymize`, identifying values are replaced by consistent pseudonyms before anything is written ([FIELD-005](../../tool-blueprint.md#285-field-data-from-production)):

| Value | Becomes |
|---|---|
| IPv4 addresses | addresses in 198.18.0.0/15. Subnets are kept, so two hosts in one /30 stay in one /30. |
| IPv6 addresses | addresses in 2001:db8::/32. Link-local addresses stay in fe80::/64. |
| MAC addresses | locally administered addresses |
| Host names (this host, the listed devices, the peer, each `sysName`) | `host-<8 hex digits>` |
| `sysContact`, `sysLocation`, `ifAlias` | every word replaced, except the classification keywords |
| Serial-number-shaped words, for example `FOC1234X5YZ` | a replacement word |

Netmasks, loopback, multicast, counters, and time ticks are left alone. The walks stay valid inputs for the simulator, and the addresses inside the IP-MIB table indexes are rewritten consistently.

**Keywords.** The words `ISP`, `WAN`, `UPLINK`, and `TRANSIT` are kept, matched as whole words and ignoring case. They are the labels vakr's description classifier looks for ([META-012](../../tool-blueprint.md#82-metadata-sites-interface-annotations-and-ownership), [Appendix E.15](../../tool-blueprint.md#e15-interface-description-keywords-meta-012)). For example, `WAN-ISP_Carrier ckt 77123` becomes `WAN-ISP_x3fa9c1 x91be02 x0c44d7`. Add your own keywords with `--keep-keyword WORD`, repeated as needed.

**The key.** Pseudonyms come from an HMAC key kept at `~/.config/vakr-fieldkit/pseudonym.key` (mode 0600). The kit creates the key on the first run and reuses it afterwards, so the same router has the same pseudonym in every bundle. The key never leaves this host, and the developers cannot reverse the pseudonyms.
- If the file is deleted, later bundles use new pseudonyms.
- If someone else can read it, the run stops.

**Limits.** Command output such as journald lines can mention names the kit has never seen, for example a domain inside a log message. Addresses, MACs, and known names are replaced there, but unknown names are not. If that matters, leave out the `vakr` profile, or review the bundle with `--no-encrypt --keep-plaintext` before you send it.

## Encryption

Bundles are encrypted by default to the development host's recipient certificate, which is built into the tool ([FIELD-006](../../tool-blueprint.md#285-field-data-from-production), ADR-028):
- the format is CMS AuthEnvelopedData with AES-256-GCM, and an RSA-3072 key with RSA-OAEP/SHA-256 key transport;
- it is produced by `openssl cms`, which needs OpenSSL 3.0 or newer (checked before collection starts).

Only the development host can open a bundle, and a bundle that was modified in transit won't decrypt. That is why it is fine to put it on any file share or web server. The plaintext `.tar.gz` is deleted after encryption unless you pass `--keep-plaintext`.

To check the built-in certificate out of band, compare its fingerprint with the one the developers give you. `manifest.json` records it too.

```text
CN=vakr field recipient
SHA-256 16:44:78:0B:BF:B9:CF:CA:28:D2:AE:F3:73:9F:14:18:59:86:0E:81:16:3E:F1:B1:E9:23:85:83:EB:07:1E:0D
```

`--no-encrypt` is only for bundles that stay on this host, for example to review the contents.

## Send it to the developers

Use either of these:

1. **scp** the file into `~/vakr-inbox/` on the development host:

   ```sh
   scp vakr-field-*.tar.gz.p7m developer@dev-host:vakr-inbox/
   ```

2. **HTTPS.** Put the file anywhere the development host can download it over HTTPS (a file share, an object store, or a web server) and send the link. The file is encrypted, so the location doesn't need to be trusted.

## On the development host

`tools/fieldkit/field_inbox.py` receives bundles ([FIELD-007, FIELD-008](../../tool-blueprint.md#285-field-data-from-production)).

```sh
# Once per development host: recipient key, certificate, and ~/vakr-inbox/
uv run tools/fieldkit/field_inbox.py init-recipient

# A link from the operator
uv run tools/fieldkit/field_inbox.py fetch https://files.example.net/vakr-field-....tar.gz.p7m --process

# Everything waiting in ~/vakr-inbox/ (scp drop-offs)
uv run tools/fieldkit/field_inbox.py process
```

For each bundle, `process`:

1. decrypts it with `~/.config/vakr-field/recipient-key.pem`, which must be mode 0600 and must stay outside the repository;
2. checks every member against the SHA-256 in `manifest.json`;
3. extracts it into the git-ignored `inbox/<bundle-id>/` of the repository, refusing absolute paths, `..`, links, and devices;
4. prints a summary: host, tool version, profiles, device count, the reachability result with the presented certificate issuer, and the step errors;
5. moves the encrypted file to `~/vakr-inbox/processed/`.

A bundle that fails any check stays where it is, and nothing is extracted. Back up the recipient key: without it, bundles that are already encrypted can't be opened.

To replace the recipient, generate a new key pair, commit the new certificate in `tools/fieldkit/recipients/vakr-dev.pem`, and release a new field kit. Old kits keep encrypting to the old key until they are replaced.

## Options and defaults

| Option | Default | Purpose |
|---|---|---|
| `--profile` | `host` | Comma-separated: `host`, `snmp`, `paths`, `vakr` |
| `--output-dir` | current directory | Where the bundle is written |
| `--time-budget` | 1800 s | Upper bound for the whole run |
| `--walk-timeout` | 60 s | Per walk and per `snmpget` |
| `--snmp-timeout`, `--snmp-retries` | 2 s, 1 | net-snmp `-t` and `-r` |
| `--device-pause` | 1 s | Pause between devices |
| `--uptime-gap` | 5 s | Minimum spacing of the two `sysUpTime` samples |
| `--max-devices` | 500 | Upper bound on the device list |
| `--ping-devices` | 5 | Devices pinged by `paths` |
| `--max-bytes`, `--max-member-bytes` | 256 MiB, 32 MiB | Uncompressed bundle and member caps |
| `--journal-lines` | 5000 | journald lines in the `vakr` profile |
| `--egress-timeout` | 15 s | Per stage of the reachability test |
| `--egress-url`, `--egress-head` | GitHub release API, releases page | Replace the reachability targets |
| `--no-strict-x509` | off (strict) | Relax only the strict X.509 profile in the reachability test |
| `--openssl` | `openssl` | OpenSSL program to use |
| `--recipient` | built in | Encrypt to a different certificate |

See [tools/fieldkit/README.md](../../tools/fieldkit/README.md) for how the tool is built and how another project can reuse it.
