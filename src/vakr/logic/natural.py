"""Natural sort order for interface, device, and position names (7.7, LIFE-007).

Operators expect ``ge-0/0/2`` before ``ge-0/0/10`` and ``Gi1/0/9`` before
``Gi1/0/10``. Plain string order gets both wrong. This key compares runs of
ASCII digits as numbers and everything else case-insensitively, then falls
back to the exact text, so the order is total and deterministic.

Dependencies:
    re: Splits names into text and digit runs.

Safety:
    Pure. Only ASCII digits count as numbers, so look-alike Unicode digits
    in a name cannot reorder the list.

Example:
    >>> sorted(["ge-0/0/10", "ge-0/0/2", "GE-0/0/1"], key=natural_key)
    ['GE-0/0/1', 'ge-0/0/2', 'ge-0/0/10']
"""

import re

_DIGITS = re.compile(r"([0-9]+)")


def natural_key(text: str) -> tuple[tuple[str | int, ...], str]:
    """Return a sort key that orders digit runs numerically.

    Splitting on digit runs always alternates text, number, text, so two
    keys only ever compare text with text and numbers with numbers.

    Args:
        text: A display name.

    Returns:
        tuple: The case-folded parts with digit runs as integers, then the
        original text as a tie-break (so ``ge-01`` and ``ge-1`` differ).

    Raises:
        None
    """
    parts = _DIGITS.split(text.casefold())
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts)), text
