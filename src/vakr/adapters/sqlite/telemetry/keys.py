"""Dictionary keys: node-local integers standing in for UUIDs in telemetry rows (ADR-021).

A telemetry row names its interface, device, and collector by small
integer keys instead of 16-byte UUIDs, which keeps keys and pages small.
Keys are assigned once, never change, and are never reused (triggers
refuse deletes), so a key read from any committed snapshot can be cached
for the life of the process. Replication and the API always carry UUIDs;
keys never leave the adapter (DB-007).

``KeyCache`` holds committed mappings both ways. The writer resolves keys
through a ``KeyAllocator``, which inserts new dictionary rows inside the
batch transaction and publishes them to the cache only after ``COMMIT``,
so a rolled-back batch can never leave a key in the cache that the file
does not have.

Dependencies:
    sqlite3, threading: Statements and the cache lock.
    vakr.adapters.sqlite.encoding: UUID blobs.

Safety:
    Fixed statements with bound values; dictionary and column names come
    from ``DICTIONARIES`` only.

Example:
    >>> cache = KeyCache()
    >>> cache.remember("device", UUID(int=7), 3)
    >>> cache.key("device", UUID(int=7)), cache.uuid("device", 3)
    (3, UUID('00000000-0000-0000-0000-000000000007'))
"""

# Dictionary table and column names below come from the fixed DICTIONARIES map.
# ruff: noqa: S608

import sqlite3
import threading
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid
from vakr.domain.errors import StorageError

DICTIONARIES: dict[str, tuple[str, str, str]] = {
    "collector": ("dict_collector", "collector_key", "collector_uuid"),
    "device": ("dict_device", "device_key", "device_uuid"),
    "interface": ("dict_interface", "interface_key", "interface_uuid"),
}
"""Dictionary name to ``(table, key column, UUID column)``."""


class KeyCache:
    """Committed UUID-to-key mappings, shared by readers and the writer.

    Attributes:
        None beyond the private maps and lock.
    """

    def __init__(self) -> None:
        """Create an empty cache.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._lock = threading.Lock()
        self._keys: dict[str, dict[UUID, int]] = {name: {} for name in DICTIONARIES}
        self._uuids: dict[str, dict[int, UUID]] = {name: {} for name in DICTIONARIES}
        self._linked: set[int] = set()

    def key(self, dictionary: str, uuid: UUID) -> int | None:
        """Return a cached key.

        Args:
            dictionary: ``collector``, ``device``, or ``interface``.
            uuid: The UUID.

        Returns:
            int | None: The key, or ``None`` if not cached.

        Raises:
            None
        """
        with self._lock:
            return self._keys[dictionary].get(uuid)

    def uuid(self, dictionary: str, key: int) -> UUID | None:
        """Return a cached UUID.

        Args:
            dictionary: The dictionary.
            key: The key.

        Returns:
            UUID | None: The UUID, or ``None`` if not cached.

        Raises:
            None
        """
        with self._lock:
            return self._uuids[dictionary].get(key)

    def remember(self, dictionary: str, uuid: UUID, key: int) -> None:
        """Cache a committed mapping.

        Args:
            dictionary: The dictionary.
            uuid: The UUID.
            key: Its key.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._keys[dictionary][uuid] = key
            self._uuids[dictionary][key] = uuid

    def linked(self, interface_key: int) -> bool:
        """Tell whether an interface's device is known to be recorded.

        Args:
            interface_key: The interface key.

        Returns:
            bool: ``True`` once its ``device_key`` is set.

        Raises:
            None
        """
        with self._lock:
            return interface_key in self._linked

    def link(self, interface_key: int) -> None:
        """Record that an interface's ``device_key`` is set.

        Args:
            interface_key: The interface key.

        Returns:
            None

        Raises:
            None
        """
        with self._lock:
            self._linked.add(interface_key)


def find_key(conn: sqlite3.Connection, cache: KeyCache, dictionary: str, uuid: UUID) -> int | None:
    """Return the key of a UUID, or ``None`` if it was never stored.

    Args:
        conn: A connection; any snapshot will do.
        cache: The shared cache.
        dictionary: The dictionary.
        uuid: The UUID.

    Returns:
        int | None: The key.

    Raises:
        sqlite3.Error: If the lookup fails; callers translate it.
    """
    key = cache.key(dictionary, uuid)
    if key is not None:
        return key
    table, key_col, uuid_col = DICTIONARIES[dictionary]
    row = conn.execute(f"SELECT {key_col} FROM {table} WHERE {uuid_col} = ?", (uuid.bytes,))
    found = row.fetchone()
    if found is None:
        return None
    cache.remember(dictionary, uuid, found[0])
    return found[0]


def find_uuid(conn: sqlite3.Connection, cache: KeyCache, dictionary: str, key: int) -> UUID:
    """Return the UUID behind a key read from a telemetry row.

    Args:
        conn: A connection.
        cache: The shared cache.
        dictionary: The dictionary.
        key: The key.

    Returns:
        UUID: The UUID.

    Raises:
        StorageError: If no dictionary row has the key (damaged storage).
        sqlite3.Error: If the lookup fails; callers translate it.
    """
    uuid = cache.uuid(dictionary, key)
    if uuid is not None:
        return uuid
    table, key_col, uuid_col = DICTIONARIES[dictionary]
    found = conn.execute(f"SELECT {uuid_col} FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    if found is None:
        raise StorageError(f"a telemetry row names a missing {dictionary} key", retryable=False)
    uuid = blob_to_uuid(found[0])
    cache.remember(dictionary, uuid, key)
    return uuid


class KeyAllocator:
    """Resolves and assigns keys inside one write transaction.

    Attributes:
        None beyond the private connection, cache, and pending maps.
    """

    def __init__(self, conn: sqlite3.Connection, cache: KeyCache) -> None:
        """Bind to a connection holding the write lock.

        Args:
            conn: Connection inside ``BEGIN IMMEDIATE``.
            cache: The shared cache.

        Returns:
            None

        Raises:
            None
        """
        self._conn, self._cache = conn, cache
        self._pending: list[tuple[str, UUID, int]] = []
        self._local: dict[tuple[str, UUID], int] = {}
        self._linked: list[int] = []

    def key(self, dictionary: str, uuid: UUID) -> int:
        """Return the key of a UUID, assigning one if it is new.

        Args:
            dictionary: The dictionary.
            uuid: The UUID.

        Returns:
            int: The key.

        Raises:
            sqlite3.Error: If the dictionary cannot be read or written.
        """
        local = self._local.get((dictionary, uuid))
        if local is not None:
            return local
        key = find_key(self._conn, self._cache, dictionary, uuid)
        if key is None:
            table, _key_col, uuid_col = DICTIONARIES[dictionary]
            cursor = self._conn.execute(
                f"INSERT INTO {table} ({uuid_col}) VALUES (?)", (uuid.bytes,)
            )
            key = int(cursor.lastrowid or 0)
            self._pending.append((dictionary, uuid, key))
        self._local[dictionary, uuid] = key
        return key

    def interface(self, uuid: UUID, device_key: int) -> int:
        """Return an interface's key and record its device if not yet known.

        Args:
            uuid: The interface UUID.
            device_key: Key of the device the sample names.

        Returns:
            int: The interface key.

        Raises:
            sqlite3.Error: If the dictionary cannot be read or written.
        """
        key = self.key("interface", uuid)
        if not self._cache.linked(key) and key not in self._linked:
            self._conn.execute(
                "UPDATE dict_interface SET device_key = ? "
                "WHERE interface_key = ? AND device_key IS NULL",
                (device_key, key),
            )
            self._linked.append(key)
        return key

    def publish(self) -> None:
        """Copy keys assigned in this transaction into the cache; call after ``COMMIT``.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        for dictionary, uuid, key in self._pending:
            self._cache.remember(dictionary, uuid, key)
        for key in self._linked:
            self._cache.link(key)
