"""Shared pieces for the observation replication tests (MPOS-004, MPOS-005).

Two SQLite telemetry stores stand for the two positions: ``source`` logs
what its spool ingests and serves it through ``ObservationSource``;
``FakePartner`` answers the puller's client calls from that source, through
a JSON round trip, so the wire form is exercised exactly as over HTTPS.

Dependencies:
    tests.contract.contract_samples, tests.unit.adapters.sqlite.telemetry.telemetry_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_observation_replication.py
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.contract import contract_samples as s
from vakr.adapters.memory import memory_uow_factory
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.replication_feed import SqliteObservationFeed
from vakr.domain.ids import CollectorId, InterfaceId, new_id
from vakr.domain.observations import Observation
from vakr.replication.serving import ObservationSource

T0, MINUTE = s.T0, s.MINUTE


def store_at(directory: Path) -> SqliteTelemetryStore:
    """Open a migrated telemetry store in its own directory.

    Args:
        directory: Where its file goes (created).

    Returns:
        SqliteTelemetryStore: The store.

    Raises:
        None
    """
    from vakr.adapters.sqlite.telemetry import telemetry_migrations

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / TELEMETRY_FILE
    telemetry_migrations(path).migrate()
    return SqliteTelemetryStore(path)


def minutes(collector: CollectorId, count: int, start: int = 0) -> list[Observation]:
    """Return one poll's worth of every family per minute for ``count`` minutes.

    Args:
        collector: The collector that made them.
        count: Minutes.
        start: First minute offset from ``T0``.

    Returns:
        list[Observation]: Five observations per minute.

    Raises:
        None
    """
    dev, iface, rows = s.device().id, new_id(InterfaceId), []
    for m in range(start, start + count):
        at = T0 + MINUTE * m
        rows += [s.sample(dev, iface, collector, at), s.icmp(dev, collector, at),
                 s.snmp(dev, collector, at), s.poll(dev, collector, at), s.health(collector, at)]  # fmt: skip
    return rows


def source_for(store: SqliteTelemetryStore, collector: CollectorId) -> ObservationSource:
    """Serve ``store``'s log for ``collector``, with its metadata record.

    Args:
        store: The source store.
        collector: Its local collector.

    Returns:
        ObservationSource: The source.

    Raises:
        None
    """
    factory = memory_uow_factory()
    with factory() as uow:
        uow.collectors.add(s.collector("east", id=collector))
        uow.commit()
    return ObservationSource(lambda: (collector,), lambda: SqliteObservationFeed(store), factory)


class FakePartner:
    """The puller's client, answered by an ``ObservationSource`` through JSON."""

    base_url = "https://east.test"

    def __init__(self, source: ObservationSource) -> None:
        """Bind the partner.

        Args:
            source: Serves the pages.

        Returns:
            None

        Raises:
            None
        """
        self.source, self.calls = source, 0

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> Any:
        """Answer one replication call.

        Args:
            path: Relative to ``/api/v1/replication/``.
            query: Its parameters.

        Returns:
            Any: The parsed answer.

        Raises:
            KeyError: For a path this fake does not serve.
        """
        self.calls += 1
        if path == "observations/state":
            answer: Any = {"role": "primary", "position": "east",
                           "collectors": self.source.state()}  # fmt: skip
        elif path == "observations" and query is not None:
            answer = self.source.page(CollectorId(UUID(query["collector"])), int(query["after"]),
                                      int(query["limit"]))  # fmt: skip
        else:
            raise KeyError(path)
        return json.loads(json.dumps(answer))


def backend(store: SqliteTelemetryStore) -> SqliteTelemetry:
    """Return the store's ports.

    Args:
        store: The store.

    Returns:
        SqliteTelemetry: Its backend.

    Raises:
        None
    """
    return SqliteTelemetry(store)
