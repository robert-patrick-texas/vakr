r"""Display order of interfaces on device pages and in the TUI (LIFE-007).

Operators open a device to look at its uplinks and WAN circuits, so those
come first: sticky interfaces, then other ``primary`` interfaces, then the
rest (META-011). Within each group a lower display rank comes first and
unranked interfaces follow; ties use natural interface order, in which
``GigabitEthernet0/0/2`` sorts before ``GigabitEthernet0/0/10`` because the
numbers compare as numbers, not text.

Dependencies:
    re: Splits interface names into text and number runs.
    vakr.policy.trace: The effective sticky and interest values.

Safety:
    Pure; ordering never changes what is collected.

Example:
    >>> natural_key("Gi0/0/2") < natural_key("Gi0/0/10")
    True
"""

import re
from collections.abc import Iterable

from vakr.domain.enums import InterestClass
from vakr.policy.facts import InterfaceFacts
from vakr.policy.trace import Decision

_RUNS = re.compile(r"(\d+)")

type NaturalKey = tuple[tuple[int, int, str], ...]
type DisplayKey = tuple[int, int, int, NaturalKey, int, str]


def natural_key(name: str) -> NaturalKey:
    """Sort key that compares the digit runs in a name as numbers.

    Args:
        name: Interface name such as ``TenGigabitEthernet1/1/1``.

    Returns:
        NaturalKey: One ``(kind, number, text)`` entry per run; text compares
        case-insensitively.

    Raises:
        None
    """
    return tuple(
        (1, int(run), "") if run.isdigit() else (0, 0, run.casefold())
        for run in _RUNS.split(name)
        if run
    )


def display_key(facts: InterfaceFacts, decision: Decision) -> DisplayKey:
    """Sort key for one interface on its device page.

    Args:
        facts: The interface.
        decision: Its effective policy decision.

    Returns:
        DisplayKey: Sticky first, then primary, then display rank (unranked
        last), then natural ``ifName`` order, then ``ifIndex``, then device.

    Raises:
        None
    """
    group = 0 if decision.sticky else 1 if decision.interest is InterestClass.PRIMARY else 2
    rank = facts.annotations.display_rank
    return (
        group,
        0 if rank is not None else 1,
        rank or 0,
        natural_key(facts.if_name or ""),
        facts.if_index or 0,
        facts.device_name,
    )


def display_order(
    items: Iterable[tuple[InterfaceFacts, Decision]],
) -> list[tuple[InterfaceFacts, Decision]]:
    """Order interfaces for a device page or TUI detail view.

    Args:
        items: Interfaces with their decisions.

    Returns:
        list[tuple[InterfaceFacts, Decision]]: The same pairs in display order.

    Raises:
        None
    """
    return sorted(items, key=lambda item: display_key(*item))
