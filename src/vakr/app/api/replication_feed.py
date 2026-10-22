"""Wire the API's observation source: identity file, telemetry feed, metadata (MPOS-004).

``observation_source`` builds the ``ObservationSource`` the replication
routes use. The telemetry store is opened on the first request that needs
it, because the API may start before the collector has created
``telemetry.db``; it stays open for the process and closes with the other
stores. This node's own collector is the one named by the collector's
identity file (MPOL-006), read at each request, so a collector registered
after the API started is served at once.

Dependencies:
    vakr.adapters.sqlite.telemetry: The store and its feed.
    vakr.app.collector.identity: The identity file.
    vakr.replication.serving: The source.

Safety:
    Read-only: the feed never takes the write lock (E.30 #12).

Example:
    >>> observation_source.__name__
    'observation_source'
"""

import threading
from dataclasses import dataclass, field

from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetryStore
from vakr.adapters.sqlite.telemetry.replication_feed import SqliteObservationFeed
from vakr.app.collector.identity import identity_path, load_identity
from vakr.domain.ids import CollectorId
from vakr.paths import InstallPaths
from vakr.ports.metadata.unit_of_work import MetadataUnitOfWorkFactory
from vakr.replication.serving import ObservationSource


@dataclass(slots=True)
class _LazyFeed:
    """Opens the telemetry store once, on first use.

    Attributes:
        paths: Install layout.
        store: The open store, or ``None``.
        lock: Serialises the first open.
    """

    paths: InstallPaths
    store: SqliteTelemetryStore | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def feed(self) -> SqliteObservationFeed | None:
        """Return the feed, opening the store if it exists.

        Args:
            None

        Returns:
            SqliteObservationFeed | None: The feed, or ``None`` without telemetry.

        Raises:
            MigrationError, StorageError: If the store exists but cannot be opened.
        """
        with self.lock:
            path = self.paths.state_dir / TELEMETRY_FILE
            if self.store is None and path.exists():
                self.store = SqliteTelemetryStore(path)
        return None if self.store is None else SqliteObservationFeed(self.store)

    def close(self) -> None:
        """Close the store if it was opened.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        with self.lock:
            if self.store is not None:
                self.store.close()
                self.store = None


def observation_source(
    paths: InstallPaths, uow_factory: MetadataUnitOfWorkFactory
) -> tuple[ObservationSource, _LazyFeed]:
    """Build the source and the closer of its store.

    Args:
        paths: Install layout.
        uow_factory: Metadata units of work.

    Returns:
        tuple: The source, and the object whose ``close`` releases the store.

    Raises:
        None
    """
    lazy = _LazyFeed(paths)

    def local_ids() -> tuple[CollectorId, ...]:
        """Return this node's collector from its identity file.

        Args:
            None

        Returns:
            tuple[CollectorId, ...]: Empty before the collector first ran.

        Raises:
            None
        """
        try:
            found = load_identity(identity_path(paths.state_dir))
        except OSError, ValueError:
            return ()
        return () if found is None else (found,)

    return ObservationSource(local_ids, lazy.feed, uow_factory), lazy
