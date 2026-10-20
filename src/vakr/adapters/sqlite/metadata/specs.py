"""Table specs: how each metadata record maps to SQLite rows (DB-001, DB-007).

A ``Spec`` tells the generic repository in ``table.py`` everything about
one record type: its table and columns, how a record becomes a row and a
row becomes a record again, its list order and filter, the set-valued
fields kept in membership tables (``Link``), the records it references,
and the fields an update may not change. Catalog records (sites, tags,
groups, policies, collectors) are defined here; devices and interfaces in
``specs_inventory``.

Dependencies:
    sqlite3: Row type.
    vakr.adapters.sqlite.connection, metadata.paging: Encodings and orders.
    vakr.domain: Records, IDs, ownership, and canonical JSON.

Safety:
    Decoding builds frozen domain records, whose own checks reject a
    tampered row; the repository reports that as damaged storage.

Example:
    >>> TAG.table, POLICY.order.columns, SITE.links[0].table
    ('tag', ('eval_order', 'name', 'id'), 'site_tag')
"""

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from vakr.adapters.sqlite.encoding import blob_to_uuid, optional_uuid
from vakr.adapters.sqlite.metadata.paging import Clause, KeyOrder, Listing, Part
from vakr.domain.catalog import Collector, Group, Tag
from vakr.domain.control import CollectionPolicy
from vakr.domain.enums import ManagedByKind
from vakr.domain.ids import CollectorId, GroupId, PolicyId, SiteId, TagId
from vakr.domain.inventory import RECORD_FIXED_FIELDS, ManagedBy, Site
from vakr.domain.json_values import canonical_json, parse_json_object

type Links = Mapping[str, frozenset[UUID]]
"""A record's set-valued fields, by attribute name, as read from membership tables."""

type Ref = tuple[str, UUID]
"""A referenced record that must exist: ``(entity, id)``."""

REF_TABLES = {"site": "site", "tag": "tag", "group": "device_group", "device": "device"}
"""Table of each entity a record can reference."""

OWNER_COLUMNS = ("managed_by_kind", "managed_by_source")
BY_NAME = KeyOrder(("name", "id"), (Part.TEXT, Part.UUID))


@dataclass(frozen=True, slots=True)
class Link:
    """A set-valued field stored as rows of a membership table.

    Attributes:
        attr: Record attribute, such as ``tags``.
        table: Membership table.
        owner: Column naming the owning record.
        member: Column naming the member.
        kind: Value of the table's ``kind`` column, when it holds several sets.
    """

    attr: str
    table: str
    owner: str
    member: str
    kind: str | None = None


def no_references(_record: Any) -> Iterable[Ref]:
    """Reference nothing.

    Args:
        _record: Any record.

    Returns:
        Iterable[Ref]: Empty.

    Raises:
        None
    """
    return ()


def no_filter(_where: Any) -> Clause:
    """Match every row; the filter of tables that cannot be filtered yet.

    Args:
        _where: Ignored ``NoFilter`` or ``None``.

    Returns:
        Clause: ``TRUE``.

    Raises:
        None
    """
    return Clause()


@dataclass(frozen=True, slots=True)
class Spec:
    """How one record type is stored.

    Attributes:
        entity: Entity name for errors, as the reference adapter names it.
        table: Table name.
        columns: Every column, ``id`` first, in ``encode`` order.
        encode: Record to column values.
        decode: Row and link sets to record.
        order: List order.
        links: Set-valued fields in membership tables.
        references: Records that must exist before this one is stored.
        where: List filter to SQL.
        fixed: Fields ``update`` may not change.
        named: ``name`` is a unique natural key.
    """

    entity: str
    table: str
    columns: tuple[str, ...]
    encode: Callable[[Any], tuple[object, ...]]
    decode: Callable[[sqlite3.Row, Links], Any]
    order: KeyOrder = BY_NAME
    links: tuple[Link, ...] = ()
    references: Callable[[Any], Iterable[Ref]] = no_references
    where: Callable[[Any], Clause] = no_filter
    fixed: tuple[str, ...] = RECORD_FIXED_FIELDS
    named: bool = True

    @property
    def listing(self) -> Listing:
        """What a list of these records reads.

        Args:
            None

        Returns:
            Listing: Table, columns, and order.

        Raises:
            None
        """
        return Listing(self.table, ", ".join(self.columns), self.order)


def owner_values(owner: ManagedBy) -> tuple[str, str]:
    """Encode an owner as its two columns.

    Args:
        owner: The owner.

    Returns:
        tuple[str, str]: Kind and source.

    Raises:
        None
    """
    return owner.kind.value, owner.source


def owner_of(row: sqlite3.Row) -> ManagedBy:
    """Decode the owner columns of a row.

    Args:
        row: A row with the owner columns.

    Returns:
        ManagedBy: The owner.

    Raises:
        ValueError: If the stored kind is unknown.
    """
    return ManagedBy(ManagedByKind(row["managed_by_kind"]), row["managed_by_source"])


def enum_value(member: StrEnum | None) -> str | None:
    """Encode an optional enum member as its stored string.

    Args:
        member: The member, or ``None``.

    Returns:
        str | None: Its value, or ``None``.

    Raises:
        None
    """
    return None if member is None else member.value


def tag_ids(values: frozenset[UUID]) -> frozenset[TagId]:
    """Type a set of tag UUIDs read from a membership table.

    Args:
        values: UUIDs.

    Returns:
        frozenset[TagId]: The same values as tag IDs.

    Raises:
        None
    """
    return frozenset(map(TagId, values))


def site_id_of(value: object) -> SiteId | None:
    """Decode an optional site reference.

    Args:
        value: Column value.

    Returns:
        SiteId | None: The site, or ``None``.

    Raises:
        StorageError: If the value is not a UUID blob.
    """
    found = optional_uuid(value)
    return None if found is None else SiteId(found)


_LABEL_COLUMNS = ("id", "name", "description", *OWNER_COLUMNS, "version")

TAG = Spec(
    "tag", "tag", _LABEL_COLUMNS,
    encode=lambda t: (t.id.bytes, t.name, t.description, *owner_values(t.managed_by), t.version),
    decode=lambda r, _: Tag(TagId(blob_to_uuid(r["id"])), r["name"], owner_of(r),
                            r["description"], r["version"]),
)  # fmt: skip

GROUP = Spec(
    "group", "device_group", _LABEL_COLUMNS,
    encode=lambda g: (g.id.bytes, g.name, g.description, *owner_values(g.managed_by), g.version),
    decode=lambda r, _: Group(GroupId(blob_to_uuid(r["id"])), r["name"], owner_of(r),
                              r["description"], r["version"]),
)  # fmt: skip

COLLECTOR = Spec(
    "collector", "collector", ("id", "name", "position", "enabled", *OWNER_COLUMNS, "version"),
    encode=lambda c: (c.id.bytes, c.name, c.position, c.enabled, *owner_values(c.managed_by),
                      c.version),
    decode=lambda r, _: Collector(CollectorId(blob_to_uuid(r["id"])), r["name"], r["position"],
                                  owner_of(r), bool(r["enabled"]), r["version"]),
)  # fmt: skip

POLICY = Spec(
    "policy", "collection_policy",
    ("id", "name", "eval_order", "scope", "rules", "enabled", *OWNER_COLUMNS, "version"),
    encode=lambda p: (p.id.bytes, p.name, p.order, p.scope, canonical_json(p.rules).decode(),
                      p.enabled, *owner_values(p.managed_by), p.version),
    decode=lambda r, _: CollectionPolicy(
        PolicyId(blob_to_uuid(r["id"])), r["name"], r["eval_order"], r["scope"],
        parse_json_object(r["rules"].encode()), owner_of(r), bool(r["enabled"]), r["version"]),
    order=KeyOrder(("eval_order", "name", "id"), (Part.INTEGER, Part.TEXT, Part.UUID)),
)  # fmt: skip

SITE = Spec(
    "site", "site",
    ("id", "name", "code", "region", "timezone", "description", *OWNER_COLUMNS, "version"),
    encode=lambda s: (s.id.bytes, s.name, s.code, s.region, s.timezone, s.description,
                      *owner_values(s.managed_by), s.version),
    decode=lambda r, links: Site(
        SiteId(blob_to_uuid(r["id"])), r["name"], r["code"], owner_of(r), r["region"],
        r["timezone"], r["description"], tag_ids(links["tags"]), r["version"]),
    links=(Link("tags", "site_tag", "site_id", "tag_id"),),
    references=lambda s: [("tag", t) for t in s.tags],
)  # fmt: skip
