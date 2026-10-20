"""This collector's identity: one UUID for metadata, spool, and observations (MPOL-006, STATE-004).

Every observation carries the UUID of the collector that made it, the spool
lives under ``/var/lib/vakr/spool/<collector UUID>/`` (E.24), and the views
find a collector's telemetry through its ``Collector`` record in the
metadata ``collectors`` table. All three use one UUID, which must survive
restarts, so it is kept in two places:

* ``/var/lib/vakr/collector/identity.json``, written atomically on the
  first start, which is the source of truth: the spool and the rate
  baselines on this disk belong to it;
* the ``Collector`` record, created from it when missing.

Without an identity file (first start, or a lost state directory) the
record with the configured ``scheduler.collector_name`` is adopted if one
exists, else a new UUID is made. A metadata record of the same name with a
different UUID is a conflict the operator must resolve; the collector
refuses to start rather than split its history.

Dependencies:
    vakr.ports.metadata: The collector repository, through a unit of work.
    vakr.app.collector.checkpoint: The atomic JSON write.

Safety:
    Blocking: call from a worker thread. Writes one record and one small file at most.

Example:
    >>> identity_path(Path("/var/lib/vakr")).name
    'identity.json'
"""

import json
from pathlib import Path
from uuid import UUID

from vakr.app.collector.checkpoint import save_checkpoint
from vakr.domain.enums import ManagedByKind
from vakr.domain.errors import DuplicateError
from vakr.domain.ids import CollectorId, new_id
from vakr.domain.inventory import Collector, ManagedBy
from vakr.ports.metadata import MetadataUnitOfWorkFactory

COLLECTOR_OWNER = ManagedBy(ManagedByKind.API, "collector")
"""Owner of records the collector registers itself; the API may edit them."""

FORMAT = "vakr-collector-identity"


class IdentityConflictError(Exception):
    """The configured collector name is registered under another UUID."""


def identity_path(state_dir: Path) -> Path:
    """Return where this node's collector identity is kept.

    Args:
        state_dir: ``/var/lib/vakr``.

    Returns:
        Path: ``<state>/collector/identity.json``.

    Raises:
        None
    """
    return state_dir / "collector" / "identity.json"


def load_identity(path: Path) -> CollectorId | None:
    """Read the persisted collector UUID.

    Args:
        path: The identity file.

    Returns:
        CollectorId | None: The UUID, or ``None`` when there is no file yet.

    Raises:
        ValueError: If the file exists but is not a valid identity file.
        OSError: If it exists but cannot be read.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"{path} is not a vakr collector identity file")
    return CollectorId(UUID(str(data["collector_id"])))


def ensure_collector(
    uow_factory: MetadataUnitOfWorkFactory, collector_id: CollectorId | None, name: str,
    position: str,
) -> Collector:  # fmt: skip
    """Return this collector's record, registering it when missing.

    Args:
        uow_factory: Units of work on the metadata database.
        collector_id: The persisted UUID, or ``None`` on a first start.
        name: ``scheduler.collector_name``.
        position: Position label for a new record (``node.position``).

    Returns:
        Collector: The stored record; an existing one is never changed.

    Raises:
        IdentityConflictError: If another UUID already holds the name.
        StorageError: If the metadata database is unavailable.
        ValidationError: If the name or position is not acceptable.
    """
    with uow_factory() as uow:
        if collector_id is not None and (found := uow.collectors.get(collector_id)) is not None:
            return found
        named = uow.collectors.get_by_name(name)
        if named is not None and collector_id is None:
            return named  # first start on this disk: adopt the registered identity
        if named is not None:
            raise IdentityConflictError(
                f"collector {name!r} is registered as {named.id}, but this node's identity "
                f"is {collector_id}; remove one of them or set another scheduler.collector_name"
            )
        record = Collector(collector_id or new_id(CollectorId), name, position, COLLECTOR_OWNER)
        try:
            uow.collectors.add(record)
            uow.commit()
        except DuplicateError:
            pass  # another process registered it between our read and write
        else:
            return record
    return ensure_collector(uow_factory, record.id, name, position)


def resolve_identity(
    uow_factory: MetadataUnitOfWorkFactory, state_dir: Path, name: str, position: str
) -> Collector:
    """Find or create this collector's identity and persist it (MPOL-006).

    Args:
        uow_factory: Units of work on the metadata database.
        state_dir: ``/var/lib/vakr``.
        name: ``scheduler.collector_name``.
        position: ``node.position``, or the name.

    Returns:
        Collector: The registered record, whose UUID is also in the identity file.

    Raises:
        IdentityConflictError: If the name belongs to another UUID in metadata.
        ValueError: If the identity file is damaged.
        OSError: If the identity file cannot be read or written.
        StorageError: If the metadata database is unavailable.
    """
    path = identity_path(state_dir)
    known = load_identity(path)
    collector = ensure_collector(uow_factory, known, name, position)
    if known != collector.id:
        data = {"format": FORMAT, "version": 1, "collector_id": str(collector.id), "name": name}
        save_checkpoint(data, path)
    return collector
