# Two positions

vakr can run as two complete nodes at two network vantage points, for example `east` and `west`. Each node polls every device from where it stands, stores what it sees, and serves the web UI, the TUI, and kiosk displays on its own. One node, the **primary**, owns inventory, accounts, and dashboards. The other, the **peer**, copies them from the primary every 60 seconds. During a partition both go on polling everything they can reach, and the peer works from its last-known copy (ADR-024, blueprint 27.5).

This release replicates **metadata**: inventory, interface identity and annotations, sites, tags, groups, policies, accounts, dashboards, and identity reviews. Replication of observations between the nodes arrives next. Until then each node's graphs show what that node collected.

Contents:

- [Planning](#planning)
- [Setting up the primary](#setting-up-the-primary)
- [Setting up the peer](#setting-up-the-peer)
- [What a peer refuses](#what-a-peer-refuses)
- [During a partition](#during-a-partition)
- [Watching replication](#watching-replication)
- [Rotating and revoking the token](#rotating-and-revoking-the-token)
- [Troubleshooting](#troubleshooting)

## Planning

| Decision | What to choose |
|---|---|
| Which node is the primary | The one people use to change inventory, accounts, and dashboards. |
| Positions | Short labels shown in every view, such as `east` and `west`: letters, digits, `.`, `_`, `-`, at most 32 characters. |
| The primary's address | An `https://` URL the peer can reach, normally the primary's reverse proxy ([web access](web-access.md)). Plain HTTP is refused. |
| TLS trust | The peer verifies the primary's certificate like any outbound HTTPS. A private CA goes in with `sudo vakr tls trust-ca` on the peer ([updates](updates.md)). |
| Proxy | `network.https_proxy` and `network.no_proxy` apply, as for self-update. |
| Release | Both nodes must run the same vakr release: the peer refuses metadata from another schema version. |

Configuration files stay node-local. Copy `/etc/vakr/policies.toml` and any `[interface_classification]` keywords from the primary to the peer, so both classify interfaces the same way.

## Setting up the primary

The primary needs nothing new beyond `vakr-api` and its reverse proxy. Give it a position, and issue the peer's token:

```console
$ sudo vakr setup --role primary --position east
$ sudo systemctl restart vakr.target
$ sudo vakr replication token create west
created replication token west, expires 2027-10-22 (id 0190...). It is shown once:

    vkr_...

On the partner node, put it alone in /etc/vakr/secrets/replication.token (mode 0640, owner root:vakr), then restart vakr.target there.
```

The token is printed exactly once. Copy it straight into the peer's file (next section); vakr never shows it again. `--lifetime 90d` shortens it, from 1 to 730 days, 365 by default.

## Setting up the peer

Install the package as usual ([install](install.md)), with an empty inventory, then:

```console
$ sudo vakr setup --role peer --position west --primary-url https://vakr-east.example.net
$ sudo install -m 0640 -o root -g vakr /dev/stdin /etc/vakr/secrets/replication.token
vkr_...
^D
$ sudo systemctl enable --now vakr.target vakr-collector.service vakr-api.service
$ sudo vakr check health
```

On a peer, `vakr setup` creates no admin account: accounts are copied from the primary, so the primary's users sign in on the peer with the same passwords. Within a minute of the collector starting, the peer holds the primary's inventory and polls it.

## What a peer refuses

A peer's metadata is replaced at every pull, so local changes are refused, naming the primary:

| Where | What is refused |
|---|---|
| API and web UI | Every change except sign-in, sign-out, kiosk display setup, and kiosk tokens: 409 `peer_read_only`, "this node is a peer; its primary is https://vakr-east.example.net: change metadata there (MPOS-003)". That covers dashboards, identity-review decisions, and manual rediscovery. |
| CLI | `vakr user add`, `passwd`, `disable`, `enable`, `role`; `vakr inventory apply`; `vakr interface claim`; `vakr interface review map` and `new`; `vakr inventory rediscover`. Each exits 1 with the same message. |
| Discovery | The peer's collector still walks its devices, but writes nothing to metadata: interface identity belongs to the primary, and the peer polls with the interfaces it copied. |

Kiosk tokens and sessions are each node's own: create a kiosk token on the node the display points at.

## During a partition

- Both nodes keep polling every device they can reach (MPOS-002).
- The peer keeps its last-known metadata and retries every 60 seconds. Changes made on the primary meanwhile arrive at the first successful pull after the partition heals.
- A failed pull is reported as node health (`replication` check, below), never as any device's state.

## Watching replication

| Where | What it shows |
|---|---|
| `sudo vakr check health` | `replication`: OK on a primary. On a peer, WARNING when the newest successful pull is 5 minutes old, CRITICAL at 30 minutes or when no pull has ever succeeded (Appendix E.13). Performance data `replication_metadata_age_s`. |
| `vakr replication status [--json]` | Role, position, the primary, and the newest pull's success, attempt, and error. |
| Self-metrics | `vakr_replication_metadata_age_seconds` and `vakr_replication_metadata_pulls_total{result}` ([self-metrics](metrics.md)). |
| Web UI and TUI headers | The position and role of the node you are looking at. |
| Journal | `journalctl -u vakr-collector`: `replication.metadata_applied` and `replication.metadata_failed` events. |

## Rotating and revoking the token

1. On the primary, `sudo vakr replication token create west-2026`.
2. On the peer, replace `/etc/vakr/secrets/replication.token` with the new token. It is read at every pull, so no restart is needed.
3. On the primary, `sudo vakr replication token revoke west` once `vakr replication status` on the peer shows a recent success.

`sudo vakr replication token list` shows labels, expiry, and last use, never the tokens. A revoked or expired token is refused at the peer's next pull.

## Troubleshooting

| The `replication` check says | Why | What to do |
|---|---|---|
| `no replication token at ...`, or `... file is readable by others` | The token file is missing or unsafe. | Install it as above: one line, mode 0640, owner `root:vakr`. |
| `partner answered HTTP 401 unauthenticated` | The token is wrong, expired, or revoked, or was issued by another node. | Issue a new one on the primary. |
| `certificate verify failed` | The peer does not trust the primary's certificate. | Trust its CA on the peer: `sudo vakr tls trust-ca FILE`. |
| `the primary runs metadata schema N and this peer M` | The releases differ. | Update the other node to the same release. |
| `node.primary_url names another peer` | The URL points at a peer. | Point it at the primary. |
| `does not match the primary's digest` (once) | Metadata changed on the primary during the pull. | Nothing: the next pull copies it whole. |
| a unique-name conflict (`name`) | Two records swapped names in one change. | Rename through an intermediate name on the primary. |
