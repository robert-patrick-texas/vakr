"""One metadata pull of a peer from its primary: state, pages, verify, apply (MPOS-003, ADR-024).

``MetadataPuller.pull_once`` asks the primary for its replicated-metadata
state. When the digest equals this peer's own, nothing changed: no pages are
read. Otherwise it reads every page of every replicated table (bounded by
E.13), and applies the snapshot in one transaction only if it hashes to the
digest the primary advertised: a snapshot torn by an edit during the pull
is discarded and pulled again next time. On any failure the peer keeps its
last-known metadata and keeps polling with it (MPOS-002). On a demoted
primary the first successful pull replaces instead, after a backup, and
reports what it discarded (``vakr.replication.resync``).

Dependencies:
    vakr.replication.client, status: The partner client and the pull record.
    vakr.ports: Units of work that offer ``replica`` (the SQLite store).

Safety:
    Blocking: run it in a worker thread. Bounded: at most ``MAX_PAGES``
    requests and ``MAX_SNAPSHOT_ROWS`` rows per pull.

Example:
    >>> MAX_PAGES, PAGE_ROWS
    (1000, 1000)
"""

import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vakr.domain.errors import DomainError, ValidationError
from vakr.lifecycle.egress.errors import EgressError
from vakr.ports.clock import Clock
from vakr.ports.metadata.replica import ReplicaPage
from vakr.replication import status
from vakr.replication.client import PartnerError, PartnerReader
from vakr.replication.resync import Resync

_LOG = logging.getLogger(__name__)
PAGE_ROWS = 1000
MAX_PAGES = 1000
"""Requests per pull, across tables; the E.13 row bound is reached long before."""
MAX_SNAPSHOT_ROWS = 500_000


class PullError(Exception):
    """A pull did not apply; the message is in operator words."""


def _page(doc: Any, table: str) -> ReplicaPage:
    """Check one page answer and convert it.

    Args:
        doc: The decoded answer.
        table: The table asked for.

    Returns:
        ReplicaPage: The page.

    Raises:
        PullError: If the answer is not a page of ``table``.
    """
    ok = isinstance(doc, dict) and doc.get("table") == table
    cols, rows = (doc.get("columns"), doc.get("rows")) if ok else (None, None)
    nxt = doc.get("next_cursor") if ok else None
    named = isinstance(cols, list) and all(isinstance(c, str) for c in cols)
    if not (named and isinstance(rows, list)
            and all(isinstance(r, list) and len(r) == len(cols) for r in rows)
            and (nxt is None or isinstance(nxt, str))):  # fmt: skip
        raise PullError(f"the primary sent a malformed {table} page")
    return ReplicaPage(table, tuple(cols), tuple(tuple(r) for r in rows), nxt)


class MetadataPuller:
    """Pulls the primary's replicated metadata into this peer."""

    def __init__(self, client: PartnerReader, uow_factory: Callable[[], Any], record: Path,
                 clock: Clock, resync: Resync | None = None) -> None:  # fmt: skip
        """Bind the puller.

        Args:
            client: The primary, with this peer's token.
            uow_factory: Opens metadata units of work that offer ``replica``.
            record: The pull record file (``status.FILE`` under the state directory).
            clock: Time source.
            resync: A demoted primary's pending resync: replace, even when unchanged.

        Returns:
            None

        Raises:
            None
        """
        self._client, self._uow, self._record, self._clock = client, uow_factory, record, clock
        self._resync = resync

    def pull_once(self) -> str:
        """Pull once and record the result.

        Args:
            None

        Returns:
            str: ``applied`` or ``unchanged``.

        Raises:
            PullError: When the pull failed; the record says why, and nothing changed.
        """
        position = None
        try:
            remote = self._client.get_json("metadata/state")
            schema, digest, position = _state(remote)
            local = self._local_state()
            _same_schema(schema, local.schema)
            if local.digest == digest:
                if self._resync is not None:
                    self._resync.report(None, remote)
                    self._resync.finish()
                status.record(self._record, "unchanged", self._clock.now_utc(), digest=digest,
                              position=position)  # fmt: skip
                return "unchanged"
            self._apply(self._snapshot(), digest, remote)
            status.record(self._record, "applied", self._clock.now_utc(), digest=digest,
                          position=position)  # fmt: skip
        except (PullError, PartnerError, EgressError, DomainError, OSError) as exc:
            why = str(exc) or type(exc).__name__
            with contextlib.suppress(OSError):  # an unwritable record must not end the pulls
                status.record(self._record, "failed", self._clock.now_utc(), error=why,
                              position=position)  # fmt: skip
            raise PullError(why) from None
        _LOG.info("applied the primary's metadata (%s)", digest[:12],
                  extra={"event_id": "replication.metadata_applied"})  # fmt: skip
        return "applied"

    def _local_state(self) -> Any:
        """Describe this peer's replicated metadata.

        Args:
            None

        Returns:
            ReplicaState: Its state.

        Raises:
            PullError: If the store cannot replicate.
        """
        with self._uow() as uow:
            replica = getattr(uow, "replica", None)
            if replica is None:
                raise PullError("this node's metadata store cannot be replicated")
            return replica.state()

    def _snapshot(self) -> dict[str, tuple[ReplicaPage, ...]]:
        """Read every page of every replicated table.

        Args:
            None

        Returns:
            dict: Pages by table.

        Raises:
            PullError: Over the E.13 bounds or for a malformed answer.
            PartnerError, EgressError: From the client.
        """
        with self._uow() as uow:
            tables = uow.replica.tables()
        snapshot: dict[str, tuple[ReplicaPage, ...]] = {}
        requests = rows = 0
        for table in tables:
            pages, cursor = [], None
            while True:
                requests += 1
                if requests > MAX_PAGES:
                    raise PullError(f"the primary's metadata needs more than {MAX_PAGES} pages")
                query: dict[str, str | int] = {"kind": table, "limit": PAGE_ROWS}
                if cursor is not None:
                    query["cursor"] = cursor
                page = _page(self._client.get_json("metadata", query), table)
                rows += len(page.rows)
                if rows > MAX_SNAPSHOT_ROWS:
                    raise PullError(
                        f"the primary's metadata exceeds {MAX_SNAPSHOT_ROWS} rows (E.13)"
                    )
                pages.append(page)
                if (cursor := page.next_cursor) is None:
                    break
            snapshot[table] = tuple(pages)
        return snapshot

    def _apply(self, snapshot: dict[str, tuple[ReplicaPage, ...]], digest: str,
               remote: Any) -> None:  # fmt: skip
        """Apply a snapshot in one transaction; replacing, after a backup, while a resync is due.

        Args:
            snapshot: Pages by table.
            digest: The primary's digest.
            remote: The primary's state answer, for the resync report.

        Returns:
            None

        Raises:
            ValidationError: Incomplete or torn snapshot (nothing written).
            StorageError: If the backup or the write fails (nothing written).
            OSError: If the resync report cannot be written (the resync is done again).
        """
        if self._resync is not None:
            self._resync.before_apply()
        with self._uow() as uow:
            result = uow.replica.apply(snapshot, digest, replace=self._resync is not None)
            if self._resync is not None:  # the report is durable before anything is discarded
                self._resync.report(result, remote)
            uow.commit()
        if self._resync is not None:
            self._resync.finish()


def _same_schema(primary: int, peer: int) -> None:
    """Refuse to apply across metadata schema versions.

    Args:
        primary: The primary's schema.
        peer: This peer's schema.

    Returns:
        None

    Raises:
        PullError: If they differ.
    """
    if primary != peer:
        raise PullError(f"the primary runs metadata schema {primary} and this peer {peer}; "
                        "run the same vakr release on both nodes")  # fmt: skip


def _state(doc: Any) -> tuple[int, str, str | None]:
    """Check the primary's state answer.

    Args:
        doc: The decoded answer.

    Returns:
        tuple: Schema, digest, and position.

    Raises:
        PullError: If it is not a state answer, or the partner is not a primary.
    """
    schema = doc.get("schema") if isinstance(doc, dict) else None
    digest = doc.get("digest") if isinstance(doc, dict) else None
    if not isinstance(schema, int) or not isinstance(digest, str) or len(digest) != 64:
        raise PullError("the primary sent a malformed metadata state")
    if doc.get("role") == "peer":
        raise PullError("node.primary_url names another peer, not the primary")
    position = doc.get("position")
    return schema, digest, position if isinstance(position, str) else None


__all__ = ["MetadataPuller", "PullError", "ValidationError"]
