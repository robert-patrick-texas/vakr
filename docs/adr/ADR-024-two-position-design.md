# ADR-024: Two positions: independent full nodes, a metadata primary, and pull replication

| Field | Value |
|---|---|
| Status | Accepted for implementation 2026-10-22; owner to confirm |
| Date | 2026-10-22 |
| Decides | How two monitoring positions share metadata and observations, who may change what, and how a pull is authorized and checked |
| Requirements | Blueprint 27.5, Appendix E.13; MPOS-001 to MPOS-008, MPOS-T01 to MPOS-T04, MPOL-001 to MPOL-007 |

## Context

The MVP's second position (milestone M5, blueprint 27.5) runs a complete vakr node at each of two network vantage points, 55 to 70 ms apart. During a partition, each node must go on polling every device it can still reach (MPOS-002). Afterwards, both must end up with complete history (MPOS-005). Views must show each position's verdict side by side (MPOS-007). Latency and counters are never merged across collectors (MPOS-008).

Blueprint 27.5 sketches the design: one metadata primary, a peer that pulls metadata every 60 s, observation replication by cursor-based pull, HTTPS, and a dedicated credential. This ADR fixes the details of part A of M5: node roles, the credential, metadata replication, and the peer's refusals. Part B, observation replication, builds on the same credential and client, and records its own decisions here when it lands.

## Decision

### Roles

1. **`[node] role` is `primary` (the default) or `peer`.** A single node is a primary without a peer, and behaves exactly as before. A peer names its primary in `node.primary_url`, which must be `https://` with no user name or query. It must also set `node.position`, so the two positions can be told apart. A primary must not set `node.primary_url`. `vakr config validate` and the startup checks of `vakr-api` and the collector enforce this (`vakr.config.node`, a rule in `vakr.config.cross_checks`).
2. **`vakr setup` sets the role** with `--role`, `--position`, and `--primary-url`, as root and before anything else, keeping `config.toml`'s comments, owner, and mode. At a terminal it asks for them on a first install, when `config.toml` exists and has no `[node]` table. On a peer, setup creates no admin: accounts come from the primary.

### The replication credential (MPOS-006)

3. **A third opaque token kind, `vkr_`.** It is like the kiosk token (E.12): 256 random bits, only the SHA-256 digest stored, labelled, created by an administrator, and expiring absolutely. It lives in its own node-local table, `replication_token` (metadata schema 6), which is never itself replicated.
   * It grants no role. Its scopes are exactly `metadata_read` and `replication_read` (`vakr.domain.replication.SCOPES`).
   * It is accepted only as `Authorization: Bearer vkr_...`, and only on `/api/v1/replication/*` (`vakr.api.replication_auth.require_scope`).
   * Anywhere else it is refused with 403 `replication_only`. Nothing else (a session, a kiosk token, a socket account) can stand in for it.
4. **Issued on the serving node, installed on the pulling node.** `sudo vakr replication token create LABEL [--lifetime DAYSd]` prints the token once. `list` shows records, never tokens or digests, and `revoke` refuses the partner's next pull.
   * Lifetime: 1 to 730 days, 365 by default. At most 10 tokens exist.
   * The pulling node reads the token from `/etc/vakr/secrets/replication.token` in strict mode, like the credentials file (SEC-016): owner root or `vakr`, readable by group `vakr` at most, opened without following symlinks, and checked on the open descriptor. It is re-read at every pull, so rotation needs no restart.
   * Each node of a pair issues one token for the other, because part B pulls observations in both directions.
5. **One client, `vakr.replication.client.PartnerClient`,** for every pull. It sends bounded GETs to the partner's `/api/v1/replication/*` over the shared egress settings: proxy, private CAs in `/etc/vakr/tls/ca.d`, and certificate and host-name checks that cannot be turned off (EGRESS-004).
   * Timeout 20 s; answers up to 8 MiB.
   * Redirects are never followed, so the token cannot travel to another host.
   * The token appears only in the `Authorization` header: never in a URL, a log line, an exception, or a `repr`.

### Metadata replication (MPOS-003)

6. **Rows, not edits.** The repositories' optimistic versions and ownership checks are about local writers, so replaying the primary's edits through them would change versions and trip ownership rules. Instead the peer copies the primary's replicated tables row for row (`vakr.adapters.sqlite.metadata.replica`), and every record keeps its UUID, its version, and its `managed_by`. The peer's change counter therefore tracks the primary's, and its collector re-plans when the counter moves.
7. **What replicates:** tags, groups, sites and their tags, collectors, collection policies, devices and their tags and groups, interfaces (identity and annotations) and their tags, identity history, local accounts (password hashes, role, enabled state), dashboards, and identity reviews.
   * Never replicated: sessions, kiosk and replication tokens, login-failure counts, audit records, and operations. They are node-local.
   * The peer's own collector record stays; the primary's is added beside it.
8. **How a table is applied.**
   * Entity tables are upserted with the primary's exact columns. An unchanged row is not touched, so the never-deleted and closed-review triggers stay quiet. Nothing is ever deleted from those tables, here or on the primary.
   * Membership tables and dashboards mirror the snapshot: rows it no longer holds are removed.
   * Identity history is append-only.
   * Foreign keys are deferred to the commit, so parents and children land together.
9. **A digest guards every pull.** `GET /api/v1/replication/metadata/state` answers the schema version, a SHA-256 over every replicated row in table and key order, the row counts, and the node's role and position. `GET /api/v1/replication/metadata?kind=&cursor=&limit=` answers one keyset page of one table (`kind` is the table, at most 1,000 rows).
   * The peer polls the state every 60 s (E.13) and pulls only when the digest differs from its own.
   * It applies the pulled pages in one transaction, and only if they hash to the digest the state advertised. A snapshot torn by an edit during the pull is discarded and pulled again, whole, next time.
   * A peer refuses a primary at another schema version ("run the same vakr release on both nodes"), and refuses a partner that says it is a peer.
10. **Bounds (E.13):** 1,000 rows per page, 1,000 page requests per pull, and 500,000 replicated rows in all, far above the 4,000-device design point.
11. **The pull runs in the peer's collector,** in a worker thread, because the collector already owns the metadata writes that discovery makes on a primary. The API process only serves.
12. **Identity belongs to the primary.** On a peer, discovery still walks devices, but its results are not written to metadata. The peer polls with the interfaces it replicated (`PeerInventory`), and manual rediscovery is refused. A device the primary has not yet discovered gets no interface counters on the peer until its interfaces arrive.

### The peer is read-only for metadata (MPOS-003, MPOS-T03)

13. **Every metadata write on a peer is refused, naming the primary:** "this node is a peer; its primary is `<primary_url>`: change metadata there".
    * In the API, `peer_guard` runs beside `csrf_guard` on every `/api/v1` route and answers 409 `peer_read_only` to any state-changing request outside `PEER_WRITES` (sign-in, sign-out, kiosk setup, and kiosk tokens, which are node-local). A new write endpoint is therefore refused on a peer until someone decides otherwise.
    * In the CLI, `refused_on_peer` wraps `vakr user add|passwd|disable|enable|role`, `vakr inventory apply`, `vakr interface claim`, `vakr interface review map|new`, and `vakr inventory rediscover`.
    * `vakr user revoke-sessions` stays allowed, because sessions are node-local.

### Health (MPOS-006)

14. **A failed pull is node health, never target state.** The collector records each attempt in `/var/lib/vakr/replication/metadata-pull.json`: the newest attempt and success, the error in operator words, and counts by result.
    * `vakr check health` and `vakr doctor` gain the `replication` check. On a peer it warns when the newest success is 5 minutes old and is critical at 30 minutes (E.13), or when no pull ever succeeded. It is OK on a primary.
    * The self-metrics gain `vakr_replication_metadata_age_seconds` and `vakr_replication_metadata_pulls_total{result}`.
    * `GET /api/v1/version` reports the role and position, which the web header and the TUI header show.

### Observation replication (MPOS-004 to MPOS-008; part B, 2026-10-22)

15. **The cursor is an ingestion sequence the telemetry store keeps, not the spool's.** 27.5 item 3 names the per-collector spool sequence as the cursor, but stored observations do not keep it, and a spool directory that is lost or recreated starts its sequence again. **Contradiction recorded; choice:** telemetry schema 3 (`0003_replication.sql`, compat `additive`) adds `repl_log`, an `AUTOINCREMENT` log of every observation the local spool ingests, in spool order, pointing at the row's key. Its `seq` is never reused, and it lives and expires with the data it indexes. The writer logs a row only when it inserts it for the first time, in the same transaction, so a replay is never served twice.
16. **A node serves only its own collector.** `GET /api/v1/replication/observations/state` lists this node's collector (its identity file) with the log's oldest and newest seq. `GET /api/v1/replication/observations?collector=&after=&limit=` answers up to 10,000 observations after `after`, oldest first, as the checksummed envelopes the spool carries (6.3), within 6 MiB. Any other collector is 404. `missing` counts log entries whose rows retention already removed. Both need the `replication_read` scope.
17. **Each node pulls the other, every 30 s, in its collector** (which owns the telemetry writes, E.30 #12). A peer pulls from `node.primary_url`, a primary from the new option `node.peer_url`. A page is checked (shapes, order, checksums, every observation the asked collector's) and stored with the cursor in one transaction (`write_replicated`). Replicated rows are ordinary rows of the partner's collector: rollups, retention, and the ranking projection treat them like local ones. They are never logged, so they are not served onward.
18. **Pacing (E.13).** At most 50,000 observations a pass, 10,000 a page. While the local spool holds more than 10,000 unacknowledged records, a pass stores at most half of what local ingestion stored since the previous pass, so backfill never takes more than half the writer and waits while local ingestion makes no progress.
19. **Gaps are counted, never hidden (MPOS-005).** When the partner no longer holds what this node has not pulled, because its oldest seq is past the cursor or a row is `missing`, the difference is a gap in the pull record and the `replication` check. A partner whose newest seq is below the cursor held here has restarted its log (a schema step-back), and is read again from the start; replays change nothing.
20. **The partner's collector gets a metadata record** on the primary the first time its observations arrive, so the views name its position. A peer receives the primary's collector with the metadata it pulls, and its own record stays local (part A).
21. **Health and visibility (27.5 item 7, MPOS-006).** The collector writes `/var/lib/vakr/replication/observation-pull.json` after every pull. The `replication` check judges the observation lag (the age of the newest partner observation held here), warning at 5 min, on gaps, or on a failing pull, and critical at 30 min. The worse of the metadata and observation halves decides. Metrics: `vakr_replication_observation_lag_seconds`, `vakr_replication_backlog`, `vakr_replication_gaps_total`, `vakr_replication_observations_total{result}`, `vakr_replication_observation_pulls_total{result}`. `HealthView.replication` puts one line in `vakr status`, the TUI header, and the web UI health panel; `vakr replication status` shows both records.
22. **Views needed nothing new.** The composite status ("Reachable from <position> only", `vakr.logic.status.composite_status`), per-position verdicts, one latency series per position, and traffic graphs on the local collector (MPOS-007, MPOS-008, 27.5 item 6) already worked per collector. Replication supplies the second collector's evidence.

## Consequences

* A peer serves views, kiosk displays, and the TUI from its last-known metadata during a partition (MPOS-002), and changes to inventory, accounts, or dashboards wait for the primary.
* Both nodes must run the same release: the metadata schema is checked at every pull. Updating the pair means updating the peer right after the primary. The peer keeps polling with its last-known metadata in between.
* Configuration files are node-local, `policies.toml` and the interface keywords among them. A peer polls with its own copies, so keep them equal to the primary's (recorded gap; the replicated policy rows feed the views only).
* The replica is implemented for the SQLite metadata store (the Compact profile). A PostgreSQL adapter will need its own implementation of `vakr.ports.metadata.replica.MetadataReplica`.
* A swap of two unique names between two records within one pull fails that apply (a unique-name conflict) and is retried. It succeeds once the primary's next state differs, or after the operator renames through an intermediate name. Recorded as a limitation.
* Part B (observation replication) reuses `PartnerClient`, the token file, and the `replication_read` scope (`vakr.api.replication_auth.ObservationReader`), under `/api/v1/replication/`.
* The replication log costs about 40 bytes per local observation, about 3 GB per node over 30 days at the MVP workload. Logging is one statement per row on the local ingest path, so the writer can tell new rows from replays: measured, one MVP minute (1,901 observations) takes 37 ms to ingest against 32 ms unlogged.
* A release rolled back onto schema 3 that predates part B keeps polling, but logs nothing, so the partner never receives what it ingested meanwhile. Releases from before read-compatible versioning refuse schema 3, so the release that ships it carries `manual-update-required`.
* A month-long partition takes at least 14 hours to backfill at the E.13 pace. The pace yields to local ingestion first.
