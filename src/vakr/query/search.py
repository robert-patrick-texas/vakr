"""Global search over the inventory index (12.1, UI-017, SEC-019).

The text is case-folded and matched as a plain substring, never as a
pattern, so no input can make matching expensive (SEC-019); it is at most
256 characters (E.29). It matches:

- devices by name, poll address, or UUID;
- interfaces by ``ifName``, ``ifAlias``, ``ifDescr``, or UUID;
- sites by name or code; providers and circuit IDs from interface
  annotations; tags and groups by name.

Each kind keeps at most ``per_kind`` hits in natural order, with the full
match count in ``totals``. Interface and circuit hits carry the interface
UUID, so a client opens the interface's full history straight from the
result (UI-017). Retired records are searchable: their history is kept
(7.5). Search results are filtered by permission in the API layer (12.1).

Dependencies:
    vakr.query.catalog_index, catalog_scope, catalog_views: Index, refs, views.

Safety:
    Pure and bounded by the index; credential names are never searched or shown.

Example:
    >>> from datetime import UTC, datetime
    >>> from vakr.query.catalog_index import CatalogIndex, Named
    >>> empty = CatalogIndex({}, (), {}, {}, {}, (), False, 0)
    >>> search_index(empty, "  ", 50, datetime(2026, 1, 1, tzinfo=UTC)).hits
    ()
"""

from collections.abc import Callable, Iterable
from datetime import datetime

from vakr.logic.natural import natural_key
from vakr.query.catalog_index import CatalogIndex, Named
from vakr.query.catalog_scope import interface_ref, ref_key
from vakr.query.catalog_views import SearchHit, SearchKind, SearchView

ORDER = (
    SearchKind.DEVICE,
    SearchKind.INTERFACE,
    SearchKind.SITE,
    SearchKind.PROVIDER,
    SearchKind.CIRCUIT,
    SearchKind.TAG,
    SearchKind.GROUP,
)
"""Kinds in the order results are listed."""


def _first(needle: str, fields: Iterable[tuple[str, str]]) -> str | None:
    """Return the name of the first field whose text contains the needle.

    Args:
        needle: Case-folded search text.
        fields: ``(field name, text)`` pairs.

    Returns:
        str | None: The matching field's name, or ``None``.

    Raises:
        None
    """
    return next((name for name, text in fields if needle in text.casefold()), None)


def _devices(index: CatalogIndex, needle: str) -> list[SearchHit]:
    """Match devices by name, poll address, or UUID.

    Args:
        index: The inventory.
        needle: Case-folded text.

    Returns:
        list[SearchHit]: Hits in natural name order.

    Raises:
        None
    """
    hits = []
    for d in sorted(index.devices.values(), key=lambda d: (natural_key(d.name), str(d.id))):
        field = _first(
            needle, (("name", d.name), ("poll_address", d.poll_address), ("id", str(d.id)))
        )
        if field:
            site = index.sites.get(d.site_id) if d.site_id else None
            hits.append(
                SearchHit(
                    SearchKind.DEVICE,
                    str(d.id),
                    d.name,
                    site.name if site else "",
                    field,
                    device_id=d.id,
                )
            )
    return hits


def _interfaces(index: CatalogIndex, needle: str) -> tuple[list[SearchHit], list[SearchHit]]:
    """Match interfaces by names, alias, description, or UUID, and circuits by circuit ID.

    Args:
        index: The inventory.
        needle: Case-folded text.

    Returns:
        tuple: Interface hits and circuit hits, each in natural order.

    Raises:
        None
    """
    found, circuits = [], []
    for i in index.interfaces:
        ref = interface_ref(index, i)
        label = f"{ref.device} {ref.name}"
        field = _first(
            needle,
            (
                ("if_name", i.if_name),
                ("if_alias", i.if_alias),
                ("if_descr", i.if_descr),
                ("id", str(i.id)),
            ),
        )
        if field:
            found.append(
                (
                    ref_key(ref),
                    SearchHit(
                        SearchKind.INTERFACE, str(i.id), label, ref.alias, field, i.device_id, i.id
                    ),
                )
            )
        circuit = i.annotation.circuit_id
        if circuit and needle in circuit.casefold():
            detail = " ".join(x for x in (ref.provider, label) if x)
            circuits.append(
                (
                    ref_key(ref),
                    SearchHit(
                        SearchKind.CIRCUIT,
                        str(i.id),
                        circuit,
                        detail,
                        "circuit_id",
                        i.device_id,
                        i.id,
                    ),
                )
            )
    return [h for _, h in sorted(found)], [h for _, h in sorted(circuits)]


def _named[R: Named](
    kind: SearchKind,
    records: Iterable[R],
    needle: str,
    fields: Callable[[R], tuple[tuple[str, str], ...]],
) -> list[SearchHit]:
    """Match named records (sites, tags, groups) in natural name order.

    Args:
        kind: The hit kind.
        records: Records with ``id`` and ``name``.
        needle: Case-folded text.
        fields: The searchable fields of a record.

    Returns:
        list[SearchHit]: Hits.

    Raises:
        None
    """
    hits = []
    for r in sorted(records, key=lambda r: natural_key(r.name)):
        field = _first(needle, fields(r))
        if field:
            hits.append(SearchHit(kind, str(r.id), r.name, "", field))
    return hits


def _providers(index: CatalogIndex, needle: str) -> list[SearchHit]:
    """Match provider names from interface annotations, once per provider.

    Args:
        index: The inventory.
        needle: Case-folded text.

    Returns:
        list[SearchHit]: One hit per matching provider, with its circuit count.

    Raises:
        None
    """
    counts: dict[str, int] = {}
    for i in index.interfaces:
        name = i.annotation.provider
        if name and needle in name.casefold():
            counts[name] = counts.get(name, 0) + 1
    return [
        SearchHit(SearchKind.PROVIDER, None, name, f"{n} interface(s)", "provider")
        for name, n in sorted(counts.items(), key=lambda kv: natural_key(kv[0]))
    ]


def search_index(index: CatalogIndex, text: str, per_kind: int, now: datetime) -> SearchView:
    """Search every kind and keep ``per_kind`` hits of each.

    Args:
        index: The inventory.
        text: Search text, at most 256 characters (checked by the caller).
        per_kind: Most hits per kind.
        now: When the view is computed (UTC).

    Returns:
        SearchView: Hits in kind order, totals, and truncation.

    Raises:
        None
    """
    needle = text.strip().casefold()
    if not needle:
        return SearchView("", (), (), index.truncated, now)
    interfaces, circuits = _interfaces(index, needle)
    by_kind = {
        SearchKind.DEVICE: _devices(index, needle),
        SearchKind.INTERFACE: interfaces,
        SearchKind.SITE: _named(
            SearchKind.SITE,
            index.sites.values(),
            needle,
            lambda s: (("name", s.name), ("code", s.code)),
        ),
        SearchKind.PROVIDER: _providers(index, needle),
        SearchKind.CIRCUIT: circuits,
        SearchKind.TAG: _named(
            SearchKind.TAG, index.tags.values(), needle, lambda t: (("name", t.name),)
        ),
        SearchKind.GROUP: _named(
            SearchKind.GROUP, index.groups.values(), needle, lambda g: (("name", g.name),)
        ),
    }
    hits = tuple(h for kind in ORDER for h in by_kind[kind][:per_kind])
    totals = tuple((kind.value, len(by_kind[kind])) for kind in ORDER)
    cut = index.truncated or any(len(by_kind[k]) > per_kind for k in ORDER)
    return SearchView(needle, hits, totals, cut, now)
