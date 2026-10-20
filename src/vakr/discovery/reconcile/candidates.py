"""Candidate gathering for rows that match neither exactly nor confidently (9.8 steps 3-10).

``DeviceEvidence`` prepares one device's rows and stored interfaces with
the device-wide evidence (name counts, strong unique MACs, unique
addresses). For an unmatched row, every unmatched stored interface sharing
its name, its ``ifIndex``, its strong MAC, or a unique address is a
candidate, and the evidence records agreement and conflict alike (9.8 step
8). No candidate means a new identity; one candidate claimed by this row
alone is *probable*; anything more is *ambiguous*. Neither merges.

Dependencies:
    vakr.discovery.reconcile.evidence, model: Evidence and results.

Safety:
    Pure.

Example:
    >>> prepare((), (), True).rows
    ()
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from vakr.discovery.model import DiscoveredInterface
from vakr.discovery.reconcile.evidence import (
    NameKey,
    hosts,
    row_key,
    same_name,
    shown,
    strong_mac,
    unique_addresses,
    unique_macs,
)
from vakr.discovery.reconcile.model import Match, Unresolved
from vakr.domain.enums import ReconcileOutcome
from vakr.domain.ids import InterfaceId
from vakr.domain.interfaces import Interface

_NEW_EVIDENCE = ("no stored interface shares its name, ifIndex, MAC, or addresses",)


@dataclass(frozen=True, slots=True)
class DeviceEvidence:
    """One device's rows and stored interfaces, with device-wide evidence.

    Attributes:
        rows: Discovered rows in ifIndex order, one per ifIndex.
        stored: Stored interfaces in UUID order.
        by_index: Stored interfaces by their current ifIndex.
        row_names: How many rows carry each name key.
        macs: Strong, unique MACs (IFID-008).
        addresses: Unique host addresses; empty when IP-MIB was incomplete.
    """

    rows: tuple[DiscoveredInterface, ...]
    stored: tuple[Interface, ...]
    by_index: dict[int, Interface]
    row_names: Counter[NameKey]
    macs: frozenset[str]
    addresses: frozenset[str]


def prepare(
    stored: Sequence[Interface], rows: Sequence[DiscoveredInterface], addresses_complete: bool
) -> DeviceEvidence:
    """Order the inputs and compute the device-wide evidence.

    Args:
        stored: The device's stored interfaces.
        rows: The discovered rows.
        addresses_complete: Whether IP-MIB answered completely.

    Returns:
        DeviceEvidence: The prepared inputs.

    Raises:
        None
    """
    unique: dict[int, DiscoveredInterface] = {}
    for row in sorted(rows, key=lambda r: r.if_index):
        unique.setdefault(row.if_index, row)
    ordered = tuple(unique.values())
    known = tuple(sorted(stored, key=lambda i: str(i.id)))
    by_index = {i.if_index: i for i in known if i.if_index is not None}
    names = Counter(k for r in ordered if (k := row_key(r)) is not None)
    addresses = unique_addresses(ordered, known) if addresses_complete else frozenset()
    return DeviceEvidence(ordered, known, by_index, names, unique_macs(ordered, known), addresses)


def _reasons(row: DiscoveredInterface, stored: Interface, dev: DeviceEvidence) -> list[str]:
    """List the evidence linking an unmatched row to one stored interface.

    Args:
        row: The row.
        stored: An unmatched stored interface.
        dev: The device.

    Returns:
        list[str]: Agreeing and conflicting evidence; empty when unrelated.

    Raises:
        None
    """
    found = []
    if same_name(row, stored):
        found.append("same name")
    if stored.if_index == row.if_index:
        found.append(f"same ifIndex {row.if_index}")
    if row.mac in dev.macs and stored.mac == row.mac:
        found.append("same unique MAC")
    if shared := sorted(hosts(row.addresses) & hosts(stored.addresses) & dev.addresses):
        found.append(f"shares address {', '.join(shared)}")
    if not found:
        return []
    if not same_name(row, stored):
        found.append(f"name differs: stored {shown(stored.if_name or stored.if_descr)}")
    if strong_mac(row.mac) and strong_mac(stored.mac) and row.mac != stored.mac:
        found.append("MAC differs")
    return found


def unresolved(
    row: DiscoveredInterface, found: dict[InterfaceId, list[str]], claims: Counter[InterfaceId],
    dev: DeviceEvidence,
) -> Match | Unresolved:  # fmt: skip
    """Decide NEW, PROBABLE, or AMBIGUOUS for a row from its candidates.

    Args:
        row: The row.
        found: Its candidates and their evidence.
        claims: How many rows name each candidate.
        dev: The device.

    Returns:
        Match | Unresolved: ``NEW`` without candidates, otherwise a case for review.

    Raises:
        None
    """
    if not found:
        return Match(row, ReconcileOutcome.NEW, None, _NEW_EVIDENCE)
    names = {i.id: i for i in dev.stored}
    evidence = [f"{shown(row.if_name or row.if_descr)} at ifIndex {row.if_index}"]
    for candidate in sorted(found, key=str):
        stored = names[candidate]
        label = f"candidate {shown(stored.if_name or stored.if_descr)} {candidate}"
        evidence.append(f"{label}: {'; '.join(found[candidate])}")
    contested = len(found) > 1 or any(claims[c] > 1 for c in found)
    if contested:
        evidence.append("more than one possible match; never merged automatically")
    outcome = ReconcileOutcome.AMBIGUOUS if contested else ReconcileOutcome.PROBABLE
    return Unresolved(row, outcome, tuple(sorted(found, key=str)), tuple(evidence))


def candidates(
    row: DiscoveredInterface, dev: DeviceEvidence, taken: set[InterfaceId]
) -> dict[InterfaceId, list[str]]:
    """Collect an unmatched row's candidates and their evidence.

    Args:
        row: The row.
        dev: The device.
        taken: Interfaces already matched.

    Returns:
        dict[InterfaceId, list[str]]: Evidence per candidate.

    Raises:
        None
    """
    found = {}
    for stored in dev.stored:
        if stored.id not in taken and (reasons := _reasons(row, stored, dev)):
            found[stored.id] = reasons
    return found
