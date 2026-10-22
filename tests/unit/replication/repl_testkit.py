"""Replication test fixtures: SQLite stores, a seeded primary, and a fake partner node.

``FakePrimary`` answers ``PartnerClient.get_json`` from a real SQLite store
the way ``vakr.api.routes_replication`` does, through a JSON round trip,
so the wire encoding of every cell (UUID blobs, hashes, JSON documents)
is exercised without a network.

Dependencies:
    tests.contract: Sample records.
    vakr.adapters.sqlite.metadata: The stores.

Example:
    >>> FakePrimary.__name__
    'FakePrimary'
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tests.contract import contract_samples as samples
from tests.contract.contract_dashboards import dashboard
from tests.unit.auth.auth_testkit import cheap_hasher
from vakr.adapters.sqlite.metadata import (
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.auth import Passwords
from vakr.auth.accounts import UserAdmin
from vakr.credentials.secret import Secret
from vakr.domain.users import Role
from vakr.ports.clock import ManualClock
from vakr.replication.metadata_pull import MetadataPuller


def open_store(path: Path) -> tuple[SqliteMetadataStore, Callable[[], Any]]:
    """Create (migrated) and open a metadata store.

    Args:
        path: The database file.

    Returns:
        tuple: The store and its unit-of-work factory.

    Raises:
        None
    """
    metadata_migrations(path).migrate()
    store = SqliteMetadataStore(path)
    return store, sqlite_uow_factory(store)


def seed(factory: Callable[[], Any], clock: ManualClock) -> dict[str, Any]:
    """Fill a primary with one of every replicated kind.

    Args:
        factory: The primary's units of work.
        clock: Time source for the account.

    Returns:
        dict[str, Any]: Record IDs by name.

    Raises:
        None
    """
    tag, group, site = samples.tag("wan"), samples.group("core"), samples.site("Dallas")
    collector = samples.collector("east")
    device = samples.device("core1", tags=frozenset({tag.id}), groups=frozenset({group.id}),
                            site_id=site.id)  # fmt: skip
    iface = samples.interface(device.id)
    board = dashboard("NOC")
    with factory() as uow:
        for repo, rec in ((uow.tags, tag), (uow.groups, group), (uow.sites, site),
                          (uow.collectors, collector), (uow.devices, device),
                          (uow.interfaces, iface)):  # fmt: skip
            repo.add(rec)
        uow.dashboards.add(board)
        uow.commit()
    admin = UserAdmin(factory, clock, Passwords(cheap_hasher()))
    admin.create("alice", Role.ADMIN, Secret("a long enough test password"), actor="test")
    return {"tag": tag.id, "group": group.id, "site": site.id, "device": device.id,
                   "iface": iface.id, "dashboard": board.id, "collector": collector.id}  # fmt: skip


class FakePrimary:
    """Answers the replication endpoints from a store, as the API does."""

    def __init__(self, factory: Callable[[], Any], *, role: str = "primary") -> None:
        """Bind to the primary's store.

        Args:
            factory: The primary's units of work.
            role: What its state says it is.

        Returns:
            None

        Raises:
            None
        """
        self.factory, self.role = factory, role
        self.calls: list[str] = []
        self.before_pages: Callable[[], None] | None = None
        self.fail: Exception | None = None

    def get_json(self, path: str, query: Mapping[str, str | int] | None = None) -> Any:
        """Serve one endpoint through a JSON round trip.

        Args:
            path: ``metadata/state`` or ``metadata``.
            query: Its parameters.

        Returns:
            Any: The decoded answer.

        Raises:
            Exception: ``fail``, when set.
        """
        self.calls.append(path)
        if self.fail is not None:
            raise self.fail
        with self.factory() as uow:
            if path == "metadata/state":
                s = uow.replica.state()
                doc: Any = {"schema": s.schema, "digest": s.digest, "rows": dict(s.rows),
                            "role": self.role, "position": "east"}  # fmt: skip
            else:
                if self.before_pages is not None:
                    hook, self.before_pages = self.before_pages, None
                    hook()
                q = dict(query or {})
                p = uow.replica.page(str(q["kind"]), q.get("cursor"), int(q["limit"]))
                doc = {"table": p.table, "columns": list(p.columns),
                       "rows": [list(r) for r in p.rows], "next_cursor": p.next_cursor}  # fmt: skip
        return json.loads(json.dumps(doc))


def puller(client: Any, factory: Callable[[], Any], record: Path) -> MetadataPuller:
    """Build a puller with a manual clock.

    Args:
        client: The (fake) partner.
        factory: The peer's units of work.
        record: The pull record file.

    Returns:
        MetadataPuller: The puller.

    Raises:
        None
    """
    from datetime import UTC, datetime

    return MetadataPuller(client, factory, record, ManualClock(datetime(2026, 10, 22, tzinfo=UTC)))
