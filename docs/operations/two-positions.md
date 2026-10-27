# Two positions

vakr can run as two complete nodes at two network vantage points, for example `east` and `west`. Each node polls every device from where it stands, stores what it sees, and serves the web UI, the TUI, and kiosk displays on its own. One node, the **primary**, owns inventory, accounts, and dashboards. The other, the **peer**, copies them from the primary every 60 seconds. During a partition both go on polling everything they can reach, and the peer works from its last-known copy (ADR-024, blueprint 27.5).

Two things replicate:

- **Metadata**, from the primary to the peer: inventory, interface identity and annotations, sites, tags, groups, policies, accounts, dashboards, and identity reviews.
- **Observations**, both ways: each node pulls the other's collector observations every 30 seconds, so every device view shows both positions' verdicts side by side, with the composite status ("Reachable from east only"), and the latency graphs show one series per position ([observation replication](#observation-replication)).

Contents:

- [Planning](#planning)
- [Setting up the primary](#setting-up-the-primary)
- [Setting up the peer](#setting-up-the-peer)
- [What a peer refuses](#what-a-peer-refuses)
- [Observation replication](#observation-replication)
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

So that the primary also pulls the peer's observations, tell it where the peer is, and install a token the **peer** issued (`sudo vakr replication token create east` on the peer) in the primary's `/etc/vakr/secrets/replication.token`:

```toml
[node]
role = "primary"
position = "east"
peer_url = "https://vakr-west.example.net"
```

Without `peer_url` the primary replicates nothing, and its views show only its own collector.

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
| API and web UI | Every change except sign-in, sign-out, kiosk display setup, and kiosk tokens: 409 `peer_read_only`, "this node is a peer; its primary is https://vakr-east.example.net: change metadata there (MPOS-003)". That covers devices and interface annotations and tags, dashboards, identity-review decisions, and manual rediscovery. |
| CLI | `vakr user add`, `passwd`, `disable`, `enable`, `role`; `vakr inventory apply`; `vakr device add`, `set`, and `remove`; `vakr interface claim`, `annotate`, and `tag`; `vakr interface review map` and `new`; `vakr inventory rediscover`. Each exits 1 with the same message. |
| Discovery | The peer's collector still walks its devices, but writes nothing to metadata: interface identity belongs to the primary, and the peer polls with the interfaces it copied. |

Kiosk tokens and sessions are each node's own: create a kiosk token on the node the display points at.

## Observation replication

Every observation belongs to the collector that made it, so the two nodes never write the same row. Each node logs what its own spool ingests, in order, and serves it from `GET /api/v1/replication/observations` to the holder of its replication token. The other node pulls every 30 seconds, after the newest sequence number it already holds (Appendix E.13, ADR-024):

- **Pulled from:** a peer pulls from `node.primary_url`, a primary from `node.peer_url`.
- **Order and duplicates:** oldest first. A page is stored with its new cursor in one transaction, and observation UUIDs make every insert idempotent, so a replay or a crash in the middle stores nothing twice and skips nothing (MPOS-004).
- **Pace:** at most 50,000 observations a pass, 10,000 a page. While the local spool holds more than 10,000 unacknowledged records, a pass stores at most half of what local ingestion stored since the previous one, so a backfill never starves local ingestion (E.13).
- **Merging:** none. Latency, loss, jitter, and counters stay per collector; graphs show one series per position, and traffic graphs default to this node's collector (MPOS-008).
- **Retention:** replicated observations are ordinary rows of the partner's collector: they are rolled up and expire like local ones.
- **Gaps:** if the partner's retention removed observations before this node could pull them, they are counted as gaps and reported in the `replication` check, never hidden (MPOS-005).

The log costs about 40 bytes per observation, about 3 GB per node over 30 days at the MVP workload, and expires with native retention. It is in telemetry schema 3 ([databases](database.md#going-back-before-telemetry-schema-3)).

A node rolled back onto schema 3 by a release that predates replication keeps polling, but it does not log what it ingests meanwhile, so the partner never receives those observations.

## During a partition

- Both nodes keep polling every device they can reach (MPOS-002).
- The peer keeps its last-known metadata and retries every 60 seconds. Changes made on the primary meanwhile arrive at the first successful pull after the partition heals.
- Each node's view of the other position goes stale: its evidence stops arriving and ages out (E.3), so a device the other position could not see reads Unknown from it, never Down.
- When the partition heals, each node backfills what the other collected, oldest first. A 30-minute partition at the MVP workload is about 57,000 observations a direction, one or two passes. A month-long one takes at least 14 hours.
- A failed pull is reported as node health (`replication` check, below), never as any device's state.

## Watching replication

| Where | What it shows |
|---|---|
| `sudo vakr check health` | `replication`: the metadata pull on a peer, WARNING when the newest successful pull is 5 minutes old, CRITICAL at 30 minutes or when none has succeeded. On a node with a partner URL, also the observation pull: WARNING while nothing has arrived, when the newest partner observation is 5 minutes old, when gaps were found, or when the newest pull failed; CRITICAL at 30 minutes (Appendix E.13). The worse of the two decides. Performance data `replication_metadata_age_s`, `replication_observation_lag_s`, `replication_backlog`, `replication_gaps`. |
| `vakr replication status [--json]` | Role, position, the partner, the newest metadata pull, and a line on observations: how far behind, the backlog, and any loss. `--json` has both records. |
| Self-metrics | `vakr_replication_metadata_age_seconds`, `vakr_replication_metadata_pulls_total{result}`, `vakr_replication_observation_lag_seconds`, `vakr_replication_backlog`, `vakr_replication_gaps_total`, `vakr_replication_observations_total{result}`, and `vakr_replication_observation_pulls_total{result}` ([self-metrics](metrics.md)). |
| `vakr status`, the TUI header, the web UI health panel | A `replication` part of the health line: for example `replication east 42 s behind (backlog 0)`. |
| Web UI and TUI headers | The position and role of the node you are looking at. |
| Journal | `journalctl -u vakr-collector`: `replication.metadata_applied`, `replication.metadata_failed`, `replication.observations`, and `replication.observations_failed` events. |

The lag is the age of the newest partner observation held here. It grows during a partition, and also when the partner itself stops polling: both are node health. A lag never changes a device's state (MPOS-006).

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
