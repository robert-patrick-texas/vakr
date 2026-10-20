"""The conservative MVP reconciliation rules (IFID-001..006, IFID-008, 9.8, E.26).

ADR-015's scoring weights wait for IOS XE field captures, so the MVP
merges histories only where no weighing is needed, and sends everything
else to an administrator (IFID-004, invariant 7). For one device:

1. **EXACT**: a stored interface has the row's ``ifIndex`` and the same
   name (``ifName``, or ``ifDescr`` when the row has no ``ifName``).
2. **CONFIDENT_RENUMBER**: the row's name belongs to exactly one stored
   interface and exactly one row, no stored interface holds the row's
   ``ifIndex``, and no strong MAC contradicts it. This also reconnects an
   interface that returns after going missing (LIFE-005).
3. Every other row gathers *candidates*: unmatched stored interfaces with
   its name, its ``ifIndex``, its strong MAC, or one of its unique
   addresses. None means **NEW**; one candidate that no other row claims
   means **PROBABLE**; anything else is **AMBIGUOUS**.
4. Stored interfaces not matched and not held as candidates are
   **MISSING**, but only when ``ifTable`` was read completely (DISC-008).

Every step compares against the stored state as it was before this
discovery, so the result does not depend on the order rows are visited
(IFID-006). There is no score: the margin over the runner-up is that the
runner-up does not exist.

Dependencies:
    vakr.discovery.reconcile.candidates, evidence, model: Candidates,
        evidence, and results.

Safety:
    Pure and deterministic: rows are visited in ifIndex order, stored
    interfaces in UUID order.

Example:
    >>> reconcile((), ()).matches
    ()
"""

from collections import Counter
from collections.abc import Sequence

from vakr.discovery.model import DiscoveredInterface
from vakr.discovery.reconcile.candidates import DeviceEvidence, candidates, prepare, unresolved
from vakr.discovery.reconcile.evidence import row_key, same_name, shown, strong_mac
from vakr.discovery.reconcile.model import Match, Reconciliation, Unresolved
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import Interface


def _exact(row: DiscoveredInterface, dev: DeviceEvidence) -> Match | None:
    """Match a row to the stored interface at its ifIndex with the same name.

    Args:
        row: The row.
        dev: The device.

    Returns:
        Match | None: An ``EXACT`` match, or ``None``.

    Raises:
        None
    """
    stored = dev.by_index.get(row.if_index)
    if stored is None or not same_name(row, stored):
        return None
    evidence = [f"same ifIndex {row.if_index} and name {shown(row.if_name or row.if_descr)}"]
    if row.if_alias is not None and row.if_alias != stored.if_alias:
        evidence.append("description (ifAlias) changed; identity unaffected")
    if strong_mac(row.mac) and strong_mac(stored.mac) and row.mac != stored.mac:
        evidence.append("ifPhysAddress changed, for example after a hardware swap")
    return Match(row, ReconcileOutcome.EXACT, stored, tuple(evidence))


def _confident(
    row: DiscoveredInterface, dev: DeviceEvidence, taken: set[InterfaceId]
) -> Match | None:
    """Match a uniquely named row whose name moved to a free ifIndex.

    Args:
        row: An unmatched row.
        dev: The device.
        taken: Interfaces already matched exactly.

    Returns:
        Match | None: A ``CONFIDENT_RENUMBER`` match, or ``None``.

    Raises:
        None
    """
    key = row_key(row)
    named = [i for i in dev.stored if same_name(row, i)]
    if key is None or dev.row_names[key] != 1 or len(named) != 1 or row.if_index in dev.by_index:
        return None
    stored = named[0]
    contradicted = strong_mac(row.mac) and strong_mac(stored.mac) and row.mac != stored.mac
    if stored.id in taken or contradicted:
        return None
    name = shown(row.if_name or row.if_descr)
    if stored.if_index is None:
        why = f"name {name} is unique and returned at ifIndex {row.if_index}"
    else:
        why = f"name {name} is unique and moved from ifIndex {stored.if_index} to {row.if_index}"
    return Match(row, ReconcileOutcome.CONFIDENT_RENUMBER, stored, (why,))


def reconcile(
    stored: Sequence[Interface],
    rows: Sequence[DiscoveredInterface],
    *,
    inventory_complete: bool = True,
    addresses_complete: bool = True,
) -> Reconciliation:
    """Reconcile one device's discovered rows with its stored interfaces.

    Args:
        stored: Every stored interface of the device, retired ones included.
        rows: The discovery's ``ifTable`` rows.
        inventory_complete: ``ifTable`` was read completely, so absence means missing.
        addresses_complete: IP-MIB answered completely, so addresses count as evidence.

    Returns:
        Reconciliation: Matches, cases for review, missing and held interfaces.

    Raises:
        None
    """
    dev = prepare(stored, rows, addresses_complete)
    decided: dict[int, Match | Unresolved] = {}
    for row in dev.rows:
        if exact := _exact(row, dev):
            decided[row.if_index] = exact
    taken = {m.interface.id for m in decided.values() if isinstance(m, Match) and m.interface}
    for row in (r for r in dev.rows if r.if_index not in decided):
        if confident := _confident(row, dev, taken):
            decided[row.if_index] = confident
    taken |= {m.interface.id for m in decided.values() if isinstance(m, Match) and m.interface}
    open_rows = [r for r in dev.rows if r.if_index not in decided]
    found = {r.if_index: candidates(r, dev, taken) for r in open_rows}
    claims = Counter(c for links in found.values() for c in links)
    for row in open_rows:
        decided[row.if_index] = unresolved(row, found[row.if_index], claims, dev)
    return _assemble(dev, decided, taken, inventory_complete)


def _assemble(
    dev: DeviceEvidence,
    decided: dict[int, Match | Unresolved],
    taken: set[InterfaceId],
    complete: bool,
) -> Reconciliation:
    """Split the decisions and derive held and missing interfaces.

    Args:
        dev: The device.
        decided: The decision per ifIndex.
        taken: Interfaces matched automatically.
        complete: Whether absence from ``ifTable`` means missing.

    Returns:
        Reconciliation: The result.

    Raises:
        None
    """
    ordered = [decided[r.if_index] for r in dev.rows]
    matches = tuple(d for d in ordered if isinstance(d, Match))
    unresolved = tuple(d for d in ordered if isinstance(d, Unresolved))
    held = tuple(sorted({c for u in unresolved for c in u.candidates}, key=str))
    missing = tuple(
        i for i in dev.stored if complete and i.id not in taken and i.id not in set(held)
    )
    return Reconciliation(matches, unresolved, missing, held)
